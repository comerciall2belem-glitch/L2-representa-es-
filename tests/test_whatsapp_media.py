import json
import os
import unittest
from unittest.mock import patch
from whatsapp_media import MediaError, validate_media, media_message_payload, provider_config, send_media, send_text


class WhatsAppMediaTests(unittest.TestCase):
    def test_payload_has_one_media_object_with_caption(self):
        payload = media_message_payload('5591999999999', 'image', 'media-123', 'Reposição disponível')
        self.assertEqual(payload['type'], 'image')
        self.assertEqual(payload['image'], {'id': 'media-123', 'caption': 'Reposição disponível'})
        self.assertNotIn('text', payload)

    def test_rejects_bad_type_content_and_limits(self):
        with self.assertRaises(MediaError):
            validate_media('a.jpg', 'image/jpeg', b'not jpeg', 'Teste')
        with self.assertRaises(MediaError):
            validate_media('a.jpg', 'image/jpeg', b'\xff\xd8\xff' + b'0' * (5*1024*1024), 'Teste')
        with self.assertRaises(MediaError):
            validate_media('a.mp4', 'video/mp4', b'not mp4', 'Teste')
        with self.assertRaises(MediaError):
            validate_media('a.png', 'image/png', b'\x89PNG\r\n\x1a\n', 'x'*1025)

    @patch.dict(os.environ, {'WHATSAPP_ACCESS_TOKEN':'mock-token',
                              'WHATSAPP_PHONE_NUMBER_ID':'12345', 'WHATSAPP_GRAPH_VERSION':'v27.0'})
    @patch('whatsapp_media._post')
    def test_upload_then_single_captioned_message(self, post):
        post.side_effect = [{'id':'media-123'}, {'messages':[{'id':'wamid.mock'}]}]
        content = b'\x00\x00\x00\x18ftypisom' + b'x' * 20
        self.assertEqual(send_media('5591999999999','clip.mp4','video/mp4',content,'Veja o lançamento'), 'wamid.mock')
        self.assertEqual(post.call_count, 2)
        self.assertTrue(post.call_args_list[0].args[0].endswith('/media'))
        self.assertTrue(post.call_args_list[1].args[0].endswith('/messages'))
        self.assertIn('/v27.0/12345/', post.call_args_list[0].args[0])
        self.assertIn('/v27.0/12345/', post.call_args_list[1].args[0])
        message = json.loads(post.call_args_list[1].args[2])
        self.assertEqual(message['video'], {'id':'media-123','caption':'Veja o lançamento'})

    def test_missing_provider_config_is_explicit(self):
        with patch.dict(os.environ, {'WHATSAPP_ACCESS_TOKEN':'', 'WHATSAPP_PHONE_NUMBER_ID':''}):
            self.assertIsNone(provider_config())
            with self.assertRaisesRegex(MediaError, 'configure o acesso'):
                send_media('5591999999999','a.jpg','image/jpeg',b'\xff\xd8\xff','Olá')

    def test_switch_needs_all_three_valid_settings(self):
        values={'WHATSAPP_ACCESS_TOKEN':'mock-token','WHATSAPP_PHONE_NUMBER_ID':'12345',
                'WHATSAPP_GRAPH_VERSION':'v27.0'}
        with patch.dict(os.environ, values):
            self.assertEqual(provider_config(), ('mock-token','12345','v27.0'))
        for missing in values:
            with self.subTest(missing=missing), patch.dict(os.environ, {**values, missing:''}):
                self.assertIsNone(provider_config())
        with patch.dict(os.environ, {**values,'WHATSAPP_GRAPH_VERSION':'https://evil.test'}):
            self.assertIsNone(provider_config())

    @patch.dict(os.environ, {'WHATSAPP_ACCESS_TOKEN':'mock-token','WHATSAPP_PHONE_NUMBER_ID':'12345','WHATSAPP_GRAPH_VERSION':'v27.0'})
    @patch('whatsapp_media._post')
    def test_text_uses_real_provider_path_and_keeps_body(self,post):
        post.return_value={'messages':[{'id':'wamid.mock'}]}
        self.assertEqual(send_text('5591999999999','Olá, segue nossa proposta.'),'wamid.mock')
        self.assertTrue(post.call_args.args[0].endswith('/12345/messages'))
        payload=json.loads(post.call_args.args[2])
        self.assertEqual(payload['text']['body'],'Olá, segue nossa proposta.')
        self.assertEqual(payload['type'],'text')

    @patch.dict(os.environ, {'WHATSAPP_ACCESS_TOKEN':'seu_token_aqui','WHATSAPP_PHONE_NUMBER_ID':'12345','WHATSAPP_GRAPH_VERSION':'v27.0'})
    def test_placeholder_is_not_live_configuration(self):
        self.assertIsNone(provider_config())
