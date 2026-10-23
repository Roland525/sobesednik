import io
import unittest
import urllib.error
from unittest.mock import patch

from desktop.discord_api import get,bot_account,invite_url


class DesktopDiscordTests(unittest.TestCase):
    def test_unauthorized_error_never_echoes_credential(self):
        token='example-credential-not-a-real-token'
        error=urllib.error.HTTPError('https://discord.com/api/v10/users/@me',401,token,{},io.BytesIO())
        with patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaises(RuntimeError) as raised: get('/users/@me',token)
        self.assertNotIn(token,str(raised.exception))
        self.assertIn('Токен',str(raised.exception))

    def test_normal_user_account_is_not_accepted(self):
        with patch('desktop.discord_api.get',return_value={'id':'123','bot':False}):
            with self.assertRaises(ValueError): bot_account('example')

    def test_invitation_uses_bot_scope_and_no_admin_permission(self):
        from urllib.parse import urlsplit,parse_qs
        query=parse_qs(urlsplit(invite_url(123)).query)
        self.assertEqual(query['scope'],['bot applications.commands'])
        self.assertFalse(int(query['permissions'][0]) & 8)
