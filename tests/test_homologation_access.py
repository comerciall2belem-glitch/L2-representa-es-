from unittest.mock import Mock, patch
import os
import homologation_access as recovery


def test_cannot_reset_production():
    connect=Mock()
    with patch.dict(os.environ,{'RENDER_SERVICE_ID':'production','L2_HOMOLOGATION_RECOVERY_ID':'a'*32,'L2_HOMOLOGATION_RECOVERY_PASSWORD':'x'*24},clear=True):
        assert recovery.recover(connect,Mock()) is False
    connect.assert_not_called()


def test_one_time_only_and_credential_is_hashed():
    con=Mock();con.__enter__=Mock(return_value=con);con.__exit__=Mock(return_value=False)
    connect=Mock(return_value=con);hash_password=Mock(return_value='hashed')
    with patch.dict(os.environ,{'RENDER_SERVICE_ID':recovery.SERVICE,'L2_HOMOLOGATION_RECOVERY_ID':'a'*32,'L2_HOMOLOGATION_RECOVERY_PASSWORD':'x'*24},clear=True):
        con.execute.return_value.fetchone.side_effect=[None,('Ana Paula',)]
        assert recovery.recover(connect,hash_password)
        assert any('must_change_password=true' in c.args[0] and c.args[1]==('hashed',) for c in con.execute.call_args_list)
        con.reset_mock();hash_password.reset_mock();con.execute.return_value.fetchone.side_effect=[(1,)]
        assert recovery.recover(connect,hash_password) is False
        hash_password.assert_not_called()
        assert not any('UPDATE app_users' in c.args[0] for c in con.execute.call_args_list)
