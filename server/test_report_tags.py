"""Tag validation and patient-scoped persistence checks without external services."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError
from fastapi import HTTPException

from server.report_tags import ReportTagsRequest


class ReportTagsTests(unittest.TestCase):
    def test_tags_are_trimmed_and_deduplicated(self):
        request = ReportTagsRequest.model_validate({"tags": [" Cardiology ", "cardiology", "Follow-up"]})
        self.assertEqual(request.tags, ["Cardiology", "Follow-up"])
        self.assertEqual(ReportTagsRequest.model_validate({"tags": []}).tags, [])

    def test_invalid_tags_are_rejected(self):
        invalid = [
            [" "], ["x" * 33], ["A,B"], ["A\nB"],
            [f"tag-{index}" for index in range(11)],
        ]
        for tags in invalid:
            with self.subTest(tags=tags), self.assertRaises(ValidationError):
                ReportTagsRequest.model_validate({"tags": tags})

    def test_update_is_scoped_to_report_and_patient(self):
        query = Mock()
        query.update.return_value = query
        query.eq.return_value = query
        query.select.return_value = query
        query.execute.return_value = types.SimpleNamespace(data=[{"id": "r1", "tags": ["Visit"]}])
        client = Mock()
        client.table.return_value = query
        supabase_stub = types.ModuleType("supabase")
        supabase_stub.Client = object
        supabase_stub.create_client = lambda *_args: client
        db_path = Path(__file__).resolve().parent / "db.py"
        spec = importlib.util.spec_from_file_location("_tags_db_test", db_path)
        db = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"supabase": supabase_stub}), patch.object(sys, "path", [str(db_path.parent), *sys.path]):
            spec.loader.exec_module(db)

        self.assertEqual(db.update_report_tags("r1", "p1", ["Visit"])["tags"], ["Visit"])
        query.update.assert_called_once_with({"tags": ["Visit"]})
        self.assertEqual(query.eq.call_args_list, [unittest.mock.call("id", "r1"), unittest.mock.call("patient_id", "p1")])
        query.execute.return_value = types.SimpleNamespace(data=[])
        self.assertIsNone(db.update_report_tags("r2", "p1", ["Visit"]))

    def test_api_returns_saved_tags_and_rejects_missing_owned_report(self):
        stubs = {}
        for module_name, attributes in {
            "preprocessing": {"ProcessFile": Mock()},
            "db": {"save_report": Mock(), "update_report_tags": Mock(return_value={"id": "r1", "tags": ["Visit"]})},
            "embedding": {"embed_report_and_labs": Mock(), "delete_report_vectors": Mock()},
            "output": {"answer_question": Mock()},
            "summarization": {"_generate_report_summary": Mock()},
        }.items():
            stub = types.ModuleType(module_name)
            for name, value in attributes.items():
                setattr(stub, name, value)
            stubs[module_name] = stub
        app_path = Path(__file__).resolve().parent / "app.py"
        spec = importlib.util.spec_from_file_location("_tags_app_test", app_path)
        app = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, stubs), patch.object(sys, "path", [str(app_path.parent), *sys.path]):
            spec.loader.exec_module(app)
            with patch.object(app, "_patient_id", return_value="p1"):
                saved = app.set_document_tags("r1", ReportTagsRequest(tags=["Visit"]))
                self.assertEqual(saved, {"id": "r1", "tags": ["Visit"]})
                stubs["db"].update_report_tags.assert_called_once_with("r1", "p1", ["Visit"])
                self.assertEqual(app._document_from_report({"id": "r1", "tags": saved["tags"]})["tags"], ["Visit"])
                stubs["db"].update_report_tags.return_value = None
                with self.assertRaises(HTTPException) as missing:
                    app.set_document_tags("another-report", ReportTagsRequest(tags=["Visit"]))
                self.assertEqual(missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
