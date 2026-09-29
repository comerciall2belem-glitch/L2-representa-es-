import json
import os
import unittest
from unittest.mock import patch
from whatsapp_media import MediaError, validate_media, media_message_payload, send_media


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
                              'WHATSAPP_PHONE_NUMBER_ID':'12345', 'WHATSAPP_GRAPH_VERSION':'v23.0'})
    @patch('whatsapp_media._post')
    def test_upload_then_single_captioned_message(self, post):
        post.side_effect = [{'id':'media-123'}, {'messages':[{'id':'wamid.mock'}]}]
        content = b'\x00\x00\x00\x18ftypisom' + b'x' * 20
        self.assertEqual(send_media('5591999999999','clip.mp4','video/mp4',content,'Veja o lançamento'), 'wamid.mock')
        self.assertEqual(post.call_count, 2)
        self.assertTrue(post.call_args_list[0].args[0].endswith('/media'))
        self.assertTrue(post.call_args_list[1].args[0].endswith('/messages'))
        message = json.loads(post.call_args_list[1].args[2])
        self.assertEqual(message['video'], {'id':'media-123','caption':'Veja o lançamento'})

    def test_missing_provider_config_is_explicit(self):
        with patch.dict(os.environ, {'WHATSAPP_ACCESS_TOKEN':'', 'WHATSAPP_PHONE_NUMBER_ID':''}):
            with self.assertRaisesRegex(MediaError, 'configure o acesso'):
                send_media('5591999999999','a.jpg','image/jpeg',b'\xff\xd8\xff','Olá')
