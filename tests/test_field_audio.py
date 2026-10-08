import os
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import field_audio as audio

class AudioSecurityTests(unittest.TestCase):
    def test_trusted_media_hosts(self):
        for url in ('https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1','https://video.xx.fbcdn.net/audio'):self.assertTrue(audio.trusted_url(url))
        for url in ('http://lookaside.fbsbx.com/x','https://lookaside.fbsbx.com.evil.test/x','https://evil.test/x','https://user:token@lookaside.fbsbx.com/x','https://lookaside.fbsbx.com:444/x','https://127.0.0.1/x'):self.assertFalse(audio.trusted_url(url))
    def test_voice_never_confirms_pending_action(self):
        for text in ('CONFIRMAR','cancelar','1','9','Faça qualquer alteração no banco'):self.assertIsNone(audio.command_text(text))
        self.assertEqual(audio.command_text('Visita Loja Alfa sem pedido.'),'Visita Loja Alfa: sem pedido')
        self.assertEqual(audio.command_text('despesa combustível R$ 100,00'),'despesa combustível R$ 100,00')
    def test_requires_explicit_enable_and_key(self):
        with patch.dict(os.environ,{'L2_FIELD_AUDIO_ENABLED':'false','OPENAI_API_KEY':'test'}):self.assertFalse(audio.ready())
        with patch.dict(os.environ,{'L2_FIELD_AUDIO_ENABLED':'true','OPENAI_API_KEY':'test'}):self.assertTrue(audio.ready())

class AudioFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_operator_no_external_processing(self):
        with patch.object(audio,'reserve',return_value=None),patch.object(audio,'transcribe',new_callable=AsyncMock) as call:
            self.assertIsNone(await audio.prepare(None,'55','m1',{},None));call.assert_not_awaited()
    async def test_duplicate_processing_does_not_bill_or_ack_early(self):
        with patch.object(audio,'reserve',return_value={'cached':True,'status':'Processing'}),patch.object(audio,'transcribe',new_callable=AsyncMock) as call:
            self.assertEqual(await audio.prepare(None,'55','m1',{},None),{'skip':True});call.assert_not_awaited()
    async def test_cached_transcript_no_second_request(self):
        with patch.object(audio,'reserve',return_value={'cached':True,'status':'Transcribed','transcript':'agenda hoje'}),patch.object(audio,'transcribe',new_callable=AsyncMock) as call:
            r=await audio.prepare(None,'55','m1',{},None);self.assertEqual(r['transcript'],'agenda hoje');call.assert_not_awaited()
    async def test_failure_stored_without_sensitive_exception(self):
        with patch.object(audio,'reserve',return_value={'key':'x','status':'Processing'}),patch.object(audio,'transcribe',side_effect=RuntimeError('secret-token')),patch.object(audio,'finish') as finish:
            r=await audio.prepare(None,'55','m1',{},None);self.assertEqual(r,{'status':'Failed'});finish.assert_called_once_with(None,'x',{'status':'Failed'})
    async def test_transcription_persisted_before_command_processing(self):
        with patch.object(audio,'reserve',return_value={'key':'x','status':'Processing'}),patch.object(audio,'transcribe',return_value='visita Alfa sem pedido'),patch.object(audio,'finish') as finish:
            r=await audio.prepare(None,'55','m1',{},None);self.assertEqual(r['status'],'Transcribed');finish.assert_called_once_with(None,'x',r)
