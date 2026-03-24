"""Tests for voice command parsing."""

from backend.app.services.voice import parse_voice_command


class TestVoiceCommands:
    def test_schedule_command(self):
        result = parse_voice_command("Please generate schedule for me")
        assert result["command"] == "schedule_generate"

    def test_progress_command(self):
        result = parse_voice_command("Show progress")
        assert result["command"] == "progress_view"

    def test_quiz_command(self):
        result = parse_voice_command("I want to start quiz")
        assert result["command"] == "quiz_start"

    def test_adapt_command(self):
        result = parse_voice_command("adapt plan now")
        assert result["command"] == "agent_adapt"

    def test_unknown_command(self):
        result = parse_voice_command("random gibberish")
        assert result["command"] == "unknown"

    def test_help_command(self):
        result = parse_voice_command("help")
        assert result["command"] == "help"
