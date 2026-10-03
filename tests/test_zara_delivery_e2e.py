import unittest
from unittest.mock import patch
from scripts.zara_e2e_mock import Harness, status_payload, run
from scripts.simulate_zara import payload
import zara


class DeliveryE2ETests(unittest.TestCase):
    def test_complete_simulated_cycle(self):
        run()

    def test_acceptance_is_not_delivery(self):
        with Harness() as h:
            self.assertEqual(h.post(payload()).status_code,200)
            item=h.outbound()
            self.assertEqual(item['deliveryStatus'],'graph_accepted')
            self.assertEqual(item['e2eStatus'],'pending')
            self.assertFalse(item['e2eReady'])
            self.assertEqual(h.confirm().status_code,409)
            self.assertEqual(h.store.execute('SELECT delivered FROM zara_messages WHERE message_id=%s',(h.mid,)).fetchone()[0],0)

    def test_duplicates_reordering_failure_and_recipient(self):
        with Harness() as h:
            h.post(payload())
            h.post(status_payload(h.mid,'delivered',phone='5591888888888'))
            self.assertEqual(h.outbound()['deliveryStatus'],'graph_accepted')
            for status in ('delivered','delivered','sent','failed','read','sent'):
                self.assertEqual(h.post(status_payload(h.mid,status)).status_code,200)
            self.assertEqual(h.outbound()['deliveryStatus'],'read')
            self.assertEqual(h.store.execute('SELECT count(*) FROM zara_delivery_events WHERE phone=%s',(h.phone,)).fetchone()[0],4)
            self.assertEqual(len(h.requests),1)
            self.assertEqual(h.outbound()['e2eStatus'],'pending')

    def test_failed_delivery_never_completes(self):
        with Harness() as h:
            h.post(payload()); h.post(status_payload(h.mid,'failed')); h.inbound_reply()
            self.assertEqual(h.outbound()['deliveryStatus'],'failed')
            self.assertEqual(h.confirm().status_code,409)

    def test_status_before_send_persistence(self):
        with Harness() as h:
            self.assertEqual(h.post(status_payload(h.mid,'read')).status_code,200)
            h.post(payload())
            self.assertEqual(h.outbound()['deliveryStatus'],'read')
            self.assertEqual(h.confirm().status_code,409)

    def test_reply_must_reference_outbound_and_get_does_not_confirm(self):
        with Harness() as h:
            h.post(payload()); h.post(status_payload(h.mid)); h.inbound_reply(linked=False)
            self.assertFalse(h.outbound()['e2eReady'])
            self.assertEqual(h.confirm().status_code,409)
            h.inbound_reply(); h.items()
            self.assertEqual(h.outbound()['e2eStatus'],'pending')
            self.assertEqual(h.confirm().status_code,200)
            self.assertEqual(h.confirm().status_code,200)

    def test_other_number_and_bad_signature_do_not_change_status(self):
        with Harness() as h:
            h.post(payload())
            h.post(status_payload(h.mid,phone_id='999'))
            data=status_payload(h.mid)
            from scripts.simulate_zara import signed
            raw,headers=signed(data,'wrong-secret')
            self.assertEqual(h.client.post('/api/zara/webhook',content=raw,headers=headers).status_code,403)
            self.assertEqual(h.outbound()['deliveryStatus'],'graph_accepted')

    def test_status_storage_failure_is_retryable(self):
        with Harness() as h:
            with patch.object(zara,'record_statuses',side_effect=RuntimeError('down')):
                self.assertEqual(h.post(status_payload(h.mid)).status_code,503)

    def test_confirmation_requires_admin(self):
        with Harness() as h:
            from fastapi import HTTPException
            with patch.object(zara,'admin',side_effect=HTTPException(403)):
                self.assertEqual(h.confirm().status_code,403)
