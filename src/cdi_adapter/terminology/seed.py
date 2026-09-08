"""Curated seed terminology for the common Indian OPD vocabulary.

Systems:
  SNOMED  http://snomed.info/sct
  LOINC   http://loinc.org
  ICD10   http://hl7.org/fhir/sid/icd-10   (secondary)
  UCUM    http://unitsofmeasure.org
"""
from __future__ import annotations

SCT = "http://snomed.info/sct"
LOINC = "http://loinc.org"
ICD10 = "http://hl7.org/fhir/sid/icd-10"

# ---- conditions / problems -------------------------------------------------
CONDITIONS: dict[str, tuple[str, str, str, str | None]] = {
    # key (normalized)          system  code        display                         icd10
    "hypertension":            (SCT, "38341003",  "Hypertensive disorder",          "I10"),
    "essential hypertension":  (SCT, "59621000",  "Essential hypertension",          "I10"),
    "htn":                     (SCT, "38341003",  "Hypertensive disorder",          "I10"),
    "type 2 diabetes mellitus":(SCT, "44054006",  "Type 2 diabetes mellitus",       "E11"),
    "type 2 diabetes":         (SCT, "44054006",  "Type 2 diabetes mellitus",       "E11"),
    "t2dm":                    (SCT, "44054006",  "Type 2 diabetes mellitus",       "E11"),
    "diabetes mellitus":       (SCT, "73211009",  "Diabetes mellitus",              "E14"),
    "dyslipidaemia":           (SCT, "370992007", "Dyslipidemia",                   "E78.5"),
    "dyslipidemia":            (SCT, "370992007", "Dyslipidemia",                   "E78.5"),
    "hyperlipidaemia":         (SCT, "55822004",  "Hyperlipidemia",                 "E78.5"),
    "fatty liver":             (SCT, "197315008", "Fatty liver",                    "K76.0"),
    "grade i fatty liver":     (SCT, "442685003", "Steatosis of liver",             "K76.0"),
    "acute appendicitis":      (SCT, "85189001",  "Acute appendicitis",             "K35.80"),
    "appendicitis":            (SCT, "74400008",  "Appendicitis",                   "K37"),
    "anaemia":                 (SCT, "271737000", "Anemia",                         "D64.9"),
    "hypothyroidism":          (SCT, "40930008",  "Hypothyroidism",                 "E03.9"),
}

# ---- symptoms / findings -------------------------------------------------
SYMPTOMS: dict[str, tuple[str, str, str]] = {
    "incision-site pain":   (SCT, "301717007", "Pain at surgical incision site"),
    "incision site pain":   (SCT, "301717007", "Pain at surgical incision site"),
    "wound healthy":        (SCT, "225552003", "Wound healing well"),
    "fever":               (SCT, "386661006", "Fever"),
    "cough":               (SCT, "49727002",  "Cough"),
    "headache":            (SCT, "25064002",  "Headache"),
    "chest pain":          (SCT, "29857009",  "Chest pain"),
    "breathlessness":      (SCT, "267036007", "Dyspnea"),
    "dyspnea":             (SCT, "267036007", "Dyspnea"),
}

# ---- procedures -------------------------------------------------------------
PROCEDURES: dict[str, tuple[str, str, str]] = {
    "laparoscopic appendectomy": (SCT, "174041007", "Laparoscopic appendectomy"),
    "appendectomy":              (SCT, "80146002",  "Appendectomy"),
    "cholecystectomy":           (SCT, "38102005",  "Cholecystectomy"),
    "laparoscopic cholecystectomy": (SCT, "45595009", "Laparoscopic cholecystectomy"),
}

# ---- lab analytes -> LOINC (+ default UCUM unit) --------------------------
LABS: dict[str, tuple[str, str, str, str | None]] = {
    "hba1c":                 (LOINC, "4548-4",  "Hemoglobin A1c/Hemoglobin.total in Blood", "%"),
    "haemoglobin a1c":       (LOINC, "4548-4",  "Hemoglobin A1c/Hemoglobin.total in Blood", "%"),
    "glycated haemoglobin":  (LOINC, "4548-4",  "Hemoglobin A1c/Hemoglobin.total in Blood", "%"),
    "fasting blood sugar":   (LOINC, "1558-6",  "Fasting glucose [Mass/volume] in Serum or Plasma", "mg/dL"),
    "fbs":                   (LOINC, "1558-6",  "Fasting glucose [Mass/volume] in Serum or Plasma", "mg/dL"),
    "fasting glucose":       (LOINC, "1558-6",  "Fasting glucose [Mass/volume] in Serum or Plasma", "mg/dL"),
    "blood sugar":           (LOINC, "2345-7",  "Glucose [Mass/volume] in Serum or Plasma", "mg/dL"),
    "random blood sugar":    (LOINC, "2345-7",  "Glucose [Mass/volume] in Serum or Plasma", "mg/dL"),
    "serum creatinine":      (LOINC, "2160-0",  "Creatinine [Mass/volume] in Serum or Plasma", "mg/dL"),
    "creatinine":            (LOINC, "2160-0",  "Creatinine [Mass/volume] in Serum or Plasma", "mg/dL"),
    "blood urea":            (LOINC, "3094-0",  "Urea nitrogen [Mass/volume] in Serum or Plasma", "mg/dL"),
    "total cholesterol":     (LOINC, "2093-3",  "Cholesterol [Mass/volume] in Serum or Plasma", "mg/dL"),
    "cholesterol":           (LOINC, "2093-3",  "Cholesterol [Mass/volume] in Serum or Plasma", "mg/dL"),
    "ldl cholesterol":       (LOINC, "2089-1",  "Cholesterol in LDL [Mass/volume] in Serum or Plasma", "mg/dL"),
    "ldl":                   (LOINC, "2089-1",  "Cholesterol in LDL [Mass/volume] in Serum or Plasma", "mg/dL"),
    "hdl cholesterol":       (LOINC, "2085-9",  "Cholesterol in HDL [Mass/volume] in Serum or Plasma", "mg/dL"),
    "hdl":                   (LOINC, "2085-9",  "Cholesterol in HDL [Mass/volume] in Serum or Plasma", "mg/dL"),
    "triglycerides":         (LOINC, "2571-8",  "Triglyceride [Mass/volume] in Serum or Plasma", "mg/dL"),
    "sgpt":                  (LOINC, "1742-6",  "Alanine aminotransferase [Enzymatic activity/volume]", "U/L"),
    "alt":                   (LOINC, "1742-6",  "Alanine aminotransferase [Enzymatic activity/volume]", "U/L"),
    "sgot":                  (LOINC, "1920-8",  "Aspartate aminotransferase [Enzymatic activity/volume]", "U/L"),
    "ast":                   (LOINC, "1920-8",  "Aspartate aminotransferase [Enzymatic activity/volume]", "U/L"),
    "tsh":                   (LOINC, "3016-3",  "Thyrotropin [Units/volume] in Serum or Plasma", "m[IU]/L"),
    "total bilirubin":       (LOINC, "1975-2",  "Bilirubin.total [Mass/volume] in Serum or Plasma", "mg/dL"),
    "haemoglobin":           (LOINC, "718-7",   "Hemoglobin [Mass/volume] in Blood", "g/dL"),
    "hemoglobin":            (LOINC, "718-7",   "Hemoglobin [Mass/volume] in Blood", "g/dL"),
    "platelet count":        (LOINC, "777-3",   "Platelets [#/volume] in Blood by Automated count", "10*3/uL"),
    "wbc count":             (LOINC, "6690-2",  "Leukocytes [#/volume] in Blood by Automated count", "10*3/uL"),
    "tlc":                   (LOINC, "6690-2",  "Leukocytes [#/volume] in Blood by Automated count", "10*3/uL"),
}

# ---- vital signs -> LOINC ------------------------------------------------
VITALS: dict[str, tuple[str, str, str, str]] = {
    "systolic_bp":  (LOINC, "8480-6",  "Systolic blood pressure", "mm[Hg]"),
    "diastolic_bp": (LOINC, "8462-4",  "Diastolic blood pressure", "mm[Hg]"),
    "bp":           (LOINC, "85354-9", "Blood pressure panel with all children optional", "mm[Hg]"),
    "heart_rate":   (LOINC, "8867-4",  "Heart rate", "/min"),
    "pulse":        (LOINC, "8867-4",  "Heart rate", "/min"),
    "resp_rate":    (LOINC, "9279-1",  "Respiratory rate", "/min"),
    "temperature":  (LOINC, "8310-5",  "Body temperature", "Cel"),
    "temp":         (LOINC, "8310-5",  "Body temperature", "Cel"),
    "spo2":         (LOINC, "59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry", "%"),
    "weight":       (LOINC, "29463-7", "Body weight", "kg"),
    "height":       (LOINC, "8302-2",  "Body height", "cm"),
    "bmi":          (LOINC, "39156-5", "Body mass index (BMI) [Ratio]", "kg/m2"),
    "pain_score":   (LOINC, "72514-3", "Pain severity - 0-10 verbal numeric rating", "{score}"),
}

# ---- medications -> SNOMED CT (substance/product) -----------------------
DRUGS: dict[str, tuple[str, str, str]] = {
    "metformin":     (SCT, "372567009", "Metformin"),
    "amlodipine":    (SCT, "386864001", "Amlodipine"),
    "atorvastatin":  (SCT, "373444002", "Atorvastatin"),
    "telmisartan":   (SCT, "395892000", "Telmisartan"),
    "losartan":      (SCT, "373567002", "Losartan"),
    "glimepiride":   (SCT, "395807002", "Glimepiride"),
    "aspirin":       (SCT, "387458008", "Aspirin"),
    "paracetamol":   (SCT, "387517004", "Paracetamol"),
    "acetaminophen": (SCT, "387517004", "Paracetamol"),
    "pantoprazole":  (SCT, "395728002", "Pantoprazole"),
    "omeprazole":    (SCT, "387137007", "Omeprazole"),
    "amoxicillin":   (SCT, "372687004", "Amoxicillin"),
    "azithromycin":  (SCT, "387531004", "Azithromycin"),
    "levothyroxine": (SCT, "710809001", "Levothyroxine"),
    "insulin":       (SCT, "325072002", "Insulin"),
}

# ---- allergen substances -> SNOMED CT ----------------------------------
ALLERGENS: dict[str, tuple[str, str, str]] = {
    "penicillin":   (SCT, "373270004", "Penicillin -class of antibiotic-"),
    "sulfa":        (SCT, "418793004", "Sulfonamide"),
    "sulphonamide": (SCT, "418793004", "Sulfonamide"),
    "aspirin":      (SCT, "387458008", "Aspirin"),
    "peanut":       (SCT, "256349002", "Peanut"),
    "iodine":       (SCT, "111088007", "Iodine"),
}

# ---- dose-form / route helpers ---------------------------------------------
FREQ_PER_DAY = {
    "od": 1, "qd": 1, "hs": 1, "once daily": 1, "1-0-0": 1, "0-0-1": 1, "0-1-0": 1,
    "bd": 2, "bid": 2, "twice daily": 2, "1-0-1": 2, "1-1-0": 2, "0-1-1": 2,
    "tds": 3, "tid": 3, "thrice daily": 3, "1-1-1": 3,
    "qid": 4, "1-1-1-1": 4,
    "sos": 0, "prn": 0,
}
ROUTE_SCT = {
    "oral": ("26643006", "Oral route"), "po": ("26643006", "Oral route"),
    "iv": ("47625008", "Intravenous route"), "im": ("78421000", "Intramuscular route"),
    "sc": ("34206005", "Subcutaneous route"), "topical": ("6064005", "Topical route"),
}

UCUM_ALIASES = {
    "mg": "mg", "g": "g", "gm": "g", "mcg": "ug", "µg": "ug", "ug": "ug",
    "ml": "mL", "cc": "mL", "l": "L",
    "%": "%", "mg/dl": "mg/dL", "g/dl": "g/dL", "u/l": "U/L", "iu/l": "[IU]/L",
    "miu/l": "m[IU]/L", "mmol/l": "mmol/L", "mmhg": "mm[Hg]", "mm hg": "mm[Hg]",
    "/min": "/min", "bpm": "/min", "kg": "kg", "cm": "cm", "f": "[degF]", "c": "Cel",
    "10^3/ul": "10*3/uL", "10*3/ul": "10*3/uL",
}
