"""SiliconFlow TTS：模拟 HTTP，不访问真实网络。"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from paper2code.config import (
    Settings,
    apply_tts_runtime,
    get_settings,
    set_settings,
    tts_status,
)
from paper2code.explain.podcast import build_podcast
from paper2code.models import Paper
from paper2code.tts.base import (
    DEFAULT_VOICE_EXPLAINER,
    DEFAULT_VOICE_HOST,
    EdgeTTSEngine,
    FallbackTTSEngine,
    NullTTSEngine,
    OpenAICompatSpeechEngine,
    SiliconFlowTTSEngine,
    chunk_for_speech,
    get_tts,
    qualify_cloud_voice,
)

KEY = "fake-tts-key-1234"
_ENV_KEYS = (
    "P2C_TTS_MODE",
    "P2C_TTS_PROVIDER",
    "P2C_TTS_API_KEY",
    "P2C_TTS_BASE_URL",
    "P2C_TTS_MODEL",
    "P2C_TTS_VOICE",
    "P2C_TTS_VOICE_ALT",
)


class _Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(url: str, code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "err", Message(), io.BytesIO(body.encode("utf-8")))


class TtsCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        import paper2code.config as cfg

        self._cfg = cfg
        self._old_keys = cfg.API_KEYS_FILE
        cfg.API_KEYS_FILE = str(Path(self.tmp.name) / "api_keys.yaml")
        self._env = {name: os.environ.pop(name, None) for name in _ENV_KEYS}
        self._prev = get_settings()
        set_settings(Settings())

    def tearDown(self) -> None:
        set_settings(self._prev)
        self._cfg.API_KEYS_FILE = self._old_keys
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()

    def _settings(self, **kwargs: object) -> Settings:
        st = Settings()
        for key, value in kwargs.items():
            setattr(st, key, value)
        set_settings(st)
        return st


class TestVoicesAndChunks(TtsCase):
    def test_preset_voices_are_alex_and_anna(self) -> None:
        self.assertEqual(DEFAULT_VOICE_HOST, "FunAudioLLM/CosyVoice2-0.5B:alex")
        self.assertEqual(DEFAULT_VOICE_EXPLAINER, "FunAudioLLM/CosyVoice2-0.5B:anna")
        self.assertEqual(qualify_cloud_voice("alex", "FunAudioLLM/CosyVoice2-0.5B", "alex"), DEFAULT_VOICE_HOST)
        self.assertEqual(
            qualify_cloud_voice("zh-CN-YunxiNeural", "FunAudioLLM/CosyVoice2-0.5B", "anna"),
            DEFAULT_VOICE_EXPLAINER,
        )

    def test_long_text_is_chunked_on_sentence_boundaries(self) -> None:
        text = "。".join("这是用来测试切块的第%d句" % i for i in range(30))
        parts = chunk_for_speech(text, 80)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 80 for part in parts))
        self.assertIn("第0句", parts[0])

    def test_hard_split_when_one_sentence_exceeds_limit(self) -> None:
        parts = chunk_for_speech("啊" * 100, 40)
        self.assertGreaterEqual(len(parts), 3)
        self.assertTrue(all(len(part) <= 40 for part in parts))


class TestSiliconFlowEngine(TtsCase):
    def test_two_voices_and_chunked_posts_are_concatenated(self) -> None:
        calls = []

        def fake_urlopen(req, timeout=0):
            body = json.loads(req.data.decode("utf-8"))
            calls.append(body)
            auth = req.get_header("Authorization")
            self.assertEqual(auth, f"Bearer {KEY}")
            self.assertTrue(req.full_url.endswith("/audio/speech"))
            return _Body(b"ID3-" + str(len(calls)).encode())

        eng = SiliconFlowTTSEngine({
            "api_key": KEY,
            "chunk_chars": 40,
            "voice": "alex",
            "voice_alt": "anna",
            "base_url": "https://api.siliconflow.cn/v1/audio/speech",
        })
        self.assertEqual(eng.base_url, "https://api.siliconflow.cn/v1")
        self.assertEqual(eng.voice_a, DEFAULT_VOICE_HOST)
        self.assertEqual(eng.voice_b, DEFAULT_VOICE_EXPLAINER)
        self.assertNotIn(KEY, json.dumps(eng.config, ensure_ascii=False))
        out = Path(self.tmp.name) / "podcast.mp3"
        with patch("urllib.request.urlopen", fake_urlopen):
            path = eng.synthesize(
                [
                    {"speaker": "A", "text": "啊" * 100},
                    {"speaker": "B", "text": "讲解人这一句很短。"},
                ],
                out,
            )
        self.assertEqual(path, str(out))
        self.assertGreaterEqual(len(calls), 3)
        self.assertTrue(all(item["voice"] == DEFAULT_VOICE_HOST for item in calls[:-1]))
        self.assertEqual(calls[-1]["voice"], DEFAULT_VOICE_EXPLAINER)
        self.assertEqual(calls[0]["model"], "FunAudioLLM/CosyVoice2-0.5B")
        self.assertTrue(out.read_bytes().startswith(b"ID3-"))
        self.assertFalse(eng.warnings)

    def test_http_error_scrubs_key_and_returns_none(self) -> None:
        def fake_urlopen(req, timeout=0):
            raise _http_error(req.full_url, 401, f"invalid {KEY}")

        eng = SiliconFlowTTSEngine({"api_key": KEY, "voice": "alex", "voice_alt": "bella"})
        out = Path(self.tmp.name) / "podcast.mp3"
        with patch("urllib.request.urlopen", fake_urlopen):
            path = eng.synthesize([{"speaker": "A", "text": "你好。"}], out)
        self.assertIsNone(path)
        self.assertFalse(out.exists())
        blob = "\n".join(eng.warnings)
        self.assertIn("云端 TTS 失败", blob)
        self.assertIn("***", blob)
        self.assertNotIn(KEY, blob)

    def test_missing_key_does_not_touch_network(self) -> None:
        eng = SiliconFlowTTSEngine({})

        def boom(req, timeout=0):
            raise AssertionError("should not call network")

        with patch("urllib.request.urlopen", boom):
            path = eng.synthesize([{"speaker": "A", "text": "你好"}], Path(self.tmp.name) / "x.mp3")
        self.assertIsNone(path)
        self.assertTrue(any("API Key" in w for w in eng.warnings))


class TestEngineOrder(TtsCase):
    def test_auto_with_key_puts_siliconflow_first(self) -> None:
        self._settings(tts_mode="auto", tts_provider="auto", tts_api_key=KEY)
        eng = get_tts()
        self.assertIsInstance(eng, FallbackTTSEngine)
        self.assertIsInstance(eng.engines[0], SiliconFlowTTSEngine)

    def test_auto_without_key_keeps_local_chain(self) -> None:
        self._settings(tts_mode="auto", tts_provider="auto", tts_api_key="")
        eng = get_tts()
        self.assertEqual([e.name for e in eng.engines], ["edge", "sapi", "placeholder"])

    def test_explicit_edge_ignores_cloud_key(self) -> None:
        self._settings(tts_mode="edge", tts_api_key=KEY, tts_provider="siliconflow")
        eng = get_tts()
        self.assertIsInstance(eng, (EdgeTTSEngine, NullTTSEngine))
        self.assertNotIsInstance(eng, SiliconFlowTTSEngine)

    def test_provider_edge_skips_cloud_even_with_key(self) -> None:
        self._settings(tts_mode="auto", tts_provider="edge", tts_api_key=KEY)
        eng = get_tts()
        self.assertEqual(eng.engines[0].name, "edge")

    def test_custom_provider_uses_openai_compat_engine(self) -> None:
        self._settings(tts_mode="auto", tts_provider="custom", tts_api_key=KEY)
        eng = get_tts()
        self.assertIsInstance(eng.engines[0], OpenAICompatSpeechEngine)
        self.assertNotIsInstance(eng.engines[0], SiliconFlowTTSEngine)

    def test_off_mode(self) -> None:
        self._settings(tts_mode="off", tts_api_key=KEY)
        self.assertEqual(get_tts().name, "null")

    def test_cloud_failure_falls_back_and_records_reason(self) -> None:
        self._settings(tts_mode="auto", tts_provider="auto", tts_api_key=KEY,
                       tts_voice="alex", tts_voice_alt="anna")

        def fake_urlopen(req, timeout=0):
            raise _http_error(req.full_url, 503, f"down {KEY}")

        out = Path(self.tmp.name) / "podcast.mp3"
        with patch("urllib.request.urlopen", fake_urlopen), \
                patch.object(EdgeTTSEngine, "available", return_value=False), \
                patch("paper2code.tts.base.SapiTTSEngine.available", return_value=False):
            eng = get_tts()
            path = eng.synthesize(
                [{"speaker": "A", "text": "主持人。"}, {"speaker": "B", "text": "讲解人。"}],
                out,
            )
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(path.endswith("_placeholder.wav"))
        self.assertTrue(Path(path).stat().st_size > 44)
        self.assertEqual(eng.name, "placeholder")
        blob = "\n".join(eng.warnings)
        self.assertIn("云端 TTS 失败", blob)
        self.assertIn("placeholder", blob)
        self.assertNotIn(KEY, blob)

    def test_named_siliconflow_without_key_still_falls_back(self) -> None:
        self._settings(tts_mode="siliconflow", tts_provider="auto", tts_api_key="")

        def boom(req, timeout=0):
            raise AssertionError("no key means no HTTP")

        out = Path(self.tmp.name) / "podcast.mp3"
        with patch("urllib.request.urlopen", boom), \
                patch.object(EdgeTTSEngine, "available", return_value=False), \
                patch("paper2code.tts.base.SapiTTSEngine.available", return_value=False):
            eng = get_tts()
            path = eng.synthesize([{"speaker": "A", "text": "只有一轮也要有声音。"}], out)
        self.assertEqual(eng.engines[0].name, "siliconflow")
        assert path is not None
        self.assertTrue(path.endswith("_placeholder.wav"))
        self.assertIn("API Key", "\n".join(eng.warnings))


class TestSettingsAndApi(TtsCase):
    def test_status_and_info_never_return_the_key(self) -> None:
        os.environ["P2C_TTS_API_KEY"] = KEY
        os.environ["P2C_TTS_VOICE_ALT"] = "anna"
        get_settings(refresh=True)
        status = tts_status()
        blob = json.dumps(status, ensure_ascii=False)
        self.assertNotIn(KEY, blob)
        self.assertTrue(status["has_key"])
        self.assertEqual(status["key_source"], "env")
        self.assertTrue(status["key_hint"].endswith("1234"))
        info = json.dumps(get_settings().to_dict(), ensure_ascii=False)
        self.assertNotIn(KEY, info)
        self.assertIn('"tts_api_key": "***"', info)

    def test_persist_writes_gitignored_file_not_the_response(self) -> None:
        apply_tts_runtime(
            api_key=KEY,
            base_url="https://api.siliconflow.cn/v1/audio/speech",
            voice="alex",
            voice_alt="anna",
            provider="siliconflow",
            persist=True,
        )
        text = Path(self._cfg.API_KEYS_FILE).read_text(encoding="utf-8")
        self.assertIn(KEY, text)
        self.assertIn('tts_base_url: "https://api.siliconflow.cn/v1"', text)
        self.assertNotIn("tts_base_url: \"https://api.siliconflow.cn/v1/audio/speech\"", text)
        status = tts_status()
        self.assertNotIn(KEY, json.dumps(status))
        self.assertEqual(status["key_source"], "file")
        self.assertEqual(get_settings().tts_voice_alt, "anna")
        # 空字符串表示「沿用已有 Key」，不能把已保存的擦掉
        apply_tts_runtime(api_key="", persist=False)
        self.assertEqual(get_settings().tts_api_key, KEY)

    def test_ping_and_generate_do_not_leak_the_key(self) -> None:
        from paper2code.api import _ping_tts, synthesize_podcast_file

        self._settings(
            tts_mode="auto", tts_provider="siliconflow", tts_api_key=KEY,
            tts_voice=DEFAULT_VOICE_HOST, tts_voice_alt=DEFAULT_VOICE_EXPLAINER,
            output_dir=Path(self.tmp.name) / "out",
        )
        explain = Path(self.tmp.name) / "out" / "DemoPaper" / "explain"
        explain.mkdir(parents=True)
        (explain / "podcast_script.md").write_text(
            "# 稿\n\n**【主持人】** 这篇论文在说什么？\n\n**【讲解人】** 它给出了一个可以复现的方法。\n",
            encoding="utf-8",
        )
        calls = []

        def fake_urlopen(req, timeout=0):
            calls.append(json.loads(req.data.decode("utf-8")))
            if req.data and b"\xe6\xb5\x8b\xe8\xaf\x95" in req.data:  # 测试
                return _Body(b"ID3ping")
            return _Body(b"ID3pod")

        with patch("urllib.request.urlopen", fake_urlopen):
            ping = _ping_tts()
            generated = synthesize_podcast_file("DemoPaper")
        self.assertTrue(ping["ok"])
        self.assertNotIn(KEY, json.dumps(ping))
        self.assertTrue(generated["ok"])
        self.assertEqual(generated["engine"], "siliconflow")
        self.assertNotIn(KEY, json.dumps(generated, ensure_ascii=False))
        voices = {item["voice"] for item in calls}
        self.assertIn(DEFAULT_VOICE_HOST, voices)
        self.assertIn(DEFAULT_VOICE_EXPLAINER, voices)
        self.assertTrue((explain / "podcast.mp3").exists())

    def test_generate_rejects_bad_id_and_filepath_key(self) -> None:
        from paper2code.api import _maybe_apply_tts, synthesize_podcast_file

        self._settings(tts_api_key="keep-me")
        _maybe_apply_tts(api_key="configs/api_keys.yaml")
        self.assertEqual(get_settings().tts_api_key, "keep-me")
        with self.assertRaises(ValueError):
            synthesize_podcast_file("../secrets")
        with self.assertRaises(FileNotFoundError):
            synthesize_podcast_file("MissingPaper")

    def test_dashboard_mentions_siliconflow_controls(self) -> None:
        from paper2code import api

        html = api._DASHBOARD_HTML
        self.assertIn("/api/tts/settings", html)
        self.assertIn("/api/tts/test", html)
        self.assertIn("/api/tts/generate", html)
        self.assertIn("ttsKey", html)
        self.assertIn("anna", html)


class TestPodcastMeta(TtsCase):
    def test_meta_records_cloud_voices_without_the_key(self) -> None:
        self._settings(
            tts_mode="auto", tts_provider="auto", tts_api_key=KEY,
            tts_voice="alex", tts_voice_alt="anna",
        )

        def fake_urlopen(req, timeout=0):
            return _Body(b"ID3meta")

        with patch("urllib.request.urlopen", fake_urlopen):
            info = build_podcast(
                Paper(id="p", title="示例论文"),
                [{"speaker": "A", "text": "主持人问一句？"}, {"speaker": "B", "text": "讲解人回答。"}],
                Path(self.tmp.name) / "explain",
            )
        meta = Path(str(info["meta"])).read_text(encoding="utf-8")
        self.assertEqual(info["engine"], "siliconflow")
        self.assertIn(DEFAULT_VOICE_HOST, meta)
        self.assertIn(DEFAULT_VOICE_EXPLAINER, meta)
        self.assertNotIn(KEY, meta)
        self.assertNotIn(KEY, "\n".join(info["warnings"]))


if __name__ == "__main__":
    unittest.main()
