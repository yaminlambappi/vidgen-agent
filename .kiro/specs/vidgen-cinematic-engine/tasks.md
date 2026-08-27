# Implementation Tasks

## Task List

- [x] 1 Add ProductionMode, VoiceAssignment, VoiceBible to models.py and extend FilmProject
  - [x] 1.1 Add ProductionMode enum to vidgen/models.py
  - [x] 1.2 Add VoiceAssignment Pydantic model to vidgen/models.py
  - [x] 1.3 Add VoiceBible Pydantic model to vidgen/models.py
  - [x] 1.4 Add production_mode and voice_bible fields to FilmProject

- [x] 2 Add production_mode field to FilmCreateRequest in main.py

- [x] 3 Implement VoiceDesignAgent in vidgen/agents.py

- [x] 4 Make StoryArchitectAgent.design_story mode-aware

- [x] 5 Make CinematographerAgent.design_cinematics mode-aware

- [x] 6 Inject automotive mandate in build_veo_generation_package

- [x] 7 Update VoiceAgent.synthesize_dialogue to consume VoiceBible

- [x] 8 Integrate VoiceDesignAgent into Orchestrator planning phase

- [x] 9 Write new tests for ProductionMode, VoiceBible, VoiceDesignAgent, and mode-aware agents

- [x] 10 Verify all tests pass and fix any regressions
