from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline

NER_MODEL_NAME = "Helios9/BioMed_NER"
NER_MIN_SCORE = 0.75  # drop low-confidence junk like "05 /" at conf=0.474

_tokenizer = AutoTokenizer.from_pretrained(NER_MODEL_NAME)
_model = AutoModelForTokenClassification.from_pretrained(NER_MODEL_NAME)
_ner_pipe = pipeline(
    "token-classification",
    model=_model,
    tokenizer=_tokenizer,
    aggregation_strategy="max",  # fixes wordpiece fragments like "nas"+"##st"+"##ric"
)


test = "Infiltration noted in right paracardiac region - Pneumonitis."
print(_ner_pipe(test))