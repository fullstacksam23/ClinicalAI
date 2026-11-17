"""Offline regression checks for the backend latency changes."""

import importlib.util
import json
import sys
import threading
import types
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd


SERVER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVER_DIR))


def load_with_stubs(name, filename, stubs):
    spec = importlib.util.spec_from_file_location(name, SERVER_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {**stubs, name: module}):
        spec.loader.exec_module(module)
    return module


def embedding_module():
    batches = []

    class Inputs(dict):
        def to(self, _device):
            return self

    class Tokenizer:
        def __call__(self, texts, **_kwargs):
            batches.append(list(texts))
            return Inputs(texts=list(texts))

    class Tensor:
        def __init__(self, texts):
            self.texts = texts

        def __getitem__(self, _index):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self

        def tolist(self):
            return [[len(text)] for text in self.texts]

    class Model:
        def to(self, _device):
            return self

        def eval(self):
            return self

        def __call__(self, **inputs):
            return types.SimpleNamespace(last_hidden_state=Tensor(inputs["texts"]))

    class Point:
        def __init__(self, id, vector, payload):
            self.id, self.vector, self.payload = id, vector, payload

    client = Mock()
    client.collection_exists.return_value = True
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    torch.inference_mode = nullcontext
    transformers = types.ModuleType("transformers")
    transformers.AutoTokenizer = types.SimpleNamespace(from_pretrained=lambda *a, **k: Tokenizer())
    transformers.AutoModel = types.SimpleNamespace(from_pretrained=lambda *a, **k: Model())
    qdrant = types.ModuleType("qdrant_client")
    qdrant.QdrantClient = lambda **_kwargs: client
    qdrant_models = types.ModuleType("qdrant_client.models")
    for name in ("Distance", "VectorParams", "Filter", "FieldCondition", "MatchValue", "MatchAny", "Range"):
        setattr(qdrant_models, name, Mock())
    qdrant_models.PointStruct = Point
    module = load_with_stubs(
        "_latency_embedding", "embedding.py",
        {"torch": torch, "transformers": transformers, "qdrant_client": qdrant,
         "qdrant_client.models": qdrant_models},
    )
    return module, client, batches


class EmbeddingTests(unittest.TestCase):
    def test_article_batches_are_bounded_and_keep_order(self):
        embedding, _, batches = embedding_module()
        texts = [f"text {i}" for i in range(70)]
        self.assertEqual(embedding.embed_documents(texts), [[len(text)] for text in texts])
        self.assertEqual([len(batch) for batch in batches], [32, 32, 6])

    def test_combined_ingest_matches_standalone_payloads_and_batches_writes(self):
        embedding, client, _ = embedding_module()
        field = lambda value: types.SimpleNamespace(value=value, flagged_for_review=False)
        fields = types.SimpleNamespace(
            patient_name=field("Example"), patient_age=field(40), patient_sex=field("F"),
            report_date=field("2026-01-01"), reported_date=field(None),
            referring_doctor=field(None), report_type=field("Lab"),
        )
        labs = pd.DataFrame([
            {"row_status": "parsed", "investigation": "Glucose", "result_value": "5",
             "result_numeric": 5, "result_unit": "mmol/L", "ref_low": 4, "ref_high": 6},
            {"row_status": "unparsed", "investigation": "Header", "result_value": ""},
        ])
        text = " ".join(["report"] * 350)
        entities = [{"entity_group": "Disease"}]
        embedding.embed_report("r1", text, fields, entities, patient_id="p1")
        embedding.embed_lab_results("r1", labs, fields, patient_id="p1")
        standalone = [point for call in client.upsert.call_args_list for point in call.kwargs["points"]]
        client.upsert.reset_mock()

        embedding.embed_report_and_labs("r1", text, labs, fields, entities, patient_id="p1")
        combined = [point for call in client.upsert.call_args_list for point in call.kwargs["points"]]
        self.assertEqual([(p.vector, p.payload) for p in combined],
                         [(p.vector, p.payload) for p in standalone])
        self.assertEqual(client.upsert.call_count, 1)
        self.assertTrue(all(p.payload["patient_id"] == "p1" for p in combined))
        client.upsert.reset_mock()
        embedding._upsert_points(combined * 40)
        self.assertEqual([len(call.kwargs["points"]) for call in client.upsert.call_args_list], [64, 56])


class TrendTests(unittest.TestCase):
    def test_unfiltered_trends_reuse_patient_scoped_rows(self):
        supabase_stub = types.ModuleType("supabase")
        supabase_stub.Client = object
        supabase_stub.create_client = lambda *_args: Mock()
        db = load_with_stubs("_latency_db", "db.py", {"supabase": supabase_stub})
        def row(name, day, value):
            return {"investigation": name, "result_numeric": value, "result_unit": "mg",
                    "ref_low": 0, "ref_high": 10, "reports": {"report_date": day}}
        rows = [row("Glucose", "2026-01-01", 4), row("Urine glucose", "2026-02-01", 5)]
        with patch.object(db, "get_lab_values", return_value=rows) as get_rows:
            trends = db.get_lab_trend("p1", None)
        get_rows.assert_called_once_with("p1", date_from=None, date_to=None)
        self.assertEqual(len(trends["Glucose"]["series"]), 2)
        self.assertEqual(trends["Glucose"]["summary"]["delta"], 1)


def retrieval_module():
    db = types.ModuleType("db")
    for name in ("get_abnormal_results", "get_lab_trend", "get_latest_lab_values",
                 "get_lab_values", "get_reports_list"):
        setattr(db, name, Mock())
    embedding = types.ModuleType("embedding")
    embedding.PatientScopedRetriever = Mock()
    return load_with_stubs("_latency_retrieval", "retrieval.py",
                           {"db": db, "embedding": embedding})


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.module = retrieval_module()
        self.engine = self.module.ClinicalRetrievalEngine.__new__(self.module.ClinicalRetrievalEngine)
        self.engine.context = self.module.PatientContext("p1")
        self.engine.vector_score_threshold = 0.3
        self.plan = types.SimpleNamespace(search_query="rewritten", limit=6)

    def test_sql_starts_before_planner_finishes_and_vector_does_not_wait_for_sql(self):
        sql_started = threading.Event()
        vector_started = threading.Event()

        def sql_run(_question):
            sql_started.set()
            if not vector_started.wait(2):
                raise AssertionError("Vector search waited for SQL")
            return {"report_metadata": []}

        def planner_run(_question):
            if not sql_started.wait(2):
                raise AssertionError("SQL waited for the planner")
            return self.plan

        def vector_run(*_args, **kwargs):
            self.assertEqual(kwargs["limit"], 6)
            vector_started.set()
            return [{"payload": {"patient_id": "p1"}}]

        self.engine.sql = types.SimpleNamespace(run=sql_run)
        self.engine.planner = types.SimpleNamespace(plan=planner_run)
        self.engine.vector = types.SimpleNamespace(search=vector_run)
        result = self.engine.retrieve("question")
        self.assertEqual(result.sql_results, {"report_metadata": []})
        self.assertEqual(result.vector_results[0]["payload"]["patient_id"], "p1")

    def test_sql_can_finish_before_planner(self):
        sql_finished = threading.Event()

        def sql_run(_question):
            sql_finished.set()
            return {"lab_results": [{"id": "lab-1"}]}

        def planner_run(_question):
            if not sql_finished.wait(2):
                raise AssertionError("SQL did not run while planning")
            return self.plan

        self.engine.sql = types.SimpleNamespace(run=sql_run)
        self.engine.planner = types.SimpleNamespace(plan=planner_run)
        self.engine.vector = types.SimpleNamespace(search=lambda *_a, **_k: [])
        self.assertEqual(self.engine.retrieve("question").sql_results["lab_results"][0]["id"], "lab-1")

    def test_sql_failure_propagates(self):
        self.engine.sql = types.SimpleNamespace(run=lambda _q: (_ for _ in ()).throw(RuntimeError("SQL failed")))
        self.engine.planner = types.SimpleNamespace(plan=lambda _q: self.plan)
        self.engine.vector = types.SimpleNamespace(search=lambda *_a, **_k: [])
        with self.assertRaisesRegex(RuntimeError, "SQL failed"):
            self.engine.retrieve("question")

    def test_planner_failure_propagates(self):
        self.engine.sql = types.SimpleNamespace(run=lambda _q: {})
        self.engine.planner = types.SimpleNamespace(plan=lambda _q: (_ for _ in ()).throw(RuntimeError("planner failed")))
        self.engine.vector = types.SimpleNamespace(search=lambda *_a, **_k: [])
        with self.assertRaisesRegex(RuntimeError, "planner failed"):
            self.engine.retrieve("question")

    def test_selector_receives_only_required_report_metadata(self):
        sent = []
        def completion(**kwargs):
            sent.append(json.loads(kwargs["user"]))
            return {
                "selected_report_ids": ["r1", "another-patient-report"],
                "needs_lab_results": False, "reason": "matched",
            }
        with patch.object(self.module, "complete_json", side_effect=completion):
            selection = self.module.ReportSelectionPlanner().select("question", [{
                "id": "r1", "report_type": "Lab", "report_date": "2026-01-01",
                "referring_doctor": "Dr A", "metadata_flagged_for_review": False,
                "summary": "Long report content", "source_type": "report_metadata",
            }])
        self.assertEqual(selection.selected_report_ids, ["r1"])
        self.assertNotIn("summary", sent[0]["reports"][0])
        self.assertNotIn("source_type", sent[0]["reports"][0])


if __name__ == "__main__":
    unittest.main()
