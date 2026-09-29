"""Recognition v2 (ARCHITECTURE §15): quality gate, regions, independent engines,
immutable evidence, grammars, grounding, disagreement and the evidence hierarchy.

Contract: pixels are evidence, OCR/HTR observes, terminology interprets, priors rank,
grounding can veto, calibration decides, humans resolve residual uncertainty.
Recognizers here return transcription only - never a code or a normalised concept.
"""
