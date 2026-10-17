import pytest
from database_connection import connection_options

DSN='postgresql://user:example@localhost/staging'

def test_timeout_is_milliseconds_not_seconds():
    assert connection_options(DSN,{'DB_TIMEOUT':'30000','DB_SSLMODE':'require'})=={'connect_timeout':30,'sslmode':'require'}

def test_timeout_rounds_up():
    assert connection_options(DSN,{'DB_TIMEOUT':'30001'})['connect_timeout']==31

def test_url_timeout_is_preserved_without_explicit_override():
    assert connection_options(DSN+'?connect_timeout=15&sslmode=verify-full',{})=={}

def test_url_ssl_is_not_downgraded():
    assert connection_options(DSN+'?sslmode=verify-full',{'DB_SSLMODE':'require'})['sslmode']=='verify-full'

def test_legacy_and_ci_without_policy_preserve_ssl():
    assert connection_options(DSN+'?sslmode=disable',{})=={'connect_timeout':30}

@pytest.mark.parametrize('value',['0','30','-1','300001','abc','30000.5',''])
def test_invalid_timeout(value):
    with pytest.raises(ValueError): connection_options(DSN,{'DB_TIMEOUT':value})

@pytest.mark.parametrize('mode',['disable','allow','prefer','Require',''])
def test_ssl_policy_cannot_allow_plaintext(mode):
    with pytest.raises(ValueError): connection_options(DSN,{'DB_SSLMODE':mode})

def test_invalid_dsn_error_does_not_leak_secret():
    with pytest.raises(ValueError) as exc: connection_options('password=secret-value invalidkey=value',{})
    assert 'secret-value' not in str(exc.value)
