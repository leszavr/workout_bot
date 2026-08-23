"""Пакет генерации программ тренировок.

Единственная точка входа — `ProgramGenerationOrchestrator` (Phase 1.2-C):

GenerationRequest → GenerationJob → ExerciseFilter → CandidatePool
→ SafetyEngine → SafeExercisePool → ProgramGenerator (AI | deterministic)
→ ProgramValidator → ProgramRepository → GenerationOutcome.

`ProgramService` из этого пакета генерацией не занимается: он только читает
сохранённые версии программ.
"""
