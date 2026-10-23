import json
import urllib.error
import urllib.request

PORTAL='https://discord.com/developers/applications'


def get(path, token):
    if not token: raise ValueError('Сначала укажите токен бота')
    req=urllib.request.Request('https://discord.com/api/v10'+path,
        headers={'Authorization':'Bot '+token,'User-Agent':'SobesednikDesktop/1.0'})
    try:
        with urllib.request.urlopen(req,timeout=20) as response: return json.load(response)
    except urllib.error.HTTPError as error:
        messages={401:'Токен не принят Discord. Скопируйте Bot Token заново.',
                  403:'У бота нет доступа. Проверьте приглашение и права.',
                  429:'Discord просит подождать. Повторите через минуту.'}
        raise RuntimeError(messages.get(error.code,'Discord временно недоступен. Код '+str(error.code))) from None
    except (OSError,ValueError): raise RuntimeError('Нет связи с Discord. Проверьте интернет.') from None


def bot_account(token):
    account=get('/users/@me',token)
    if not account.get('bot'): raise ValueError('Нужен токен бота, а не пользовательского аккаунта')
    return account


def invite_url(app_id):
    return ('https://discord.com/oauth2/authorize?client_id='+str(int(app_id))+
            '&permissions=3148800&integration_type=0&scope=bot%20applications.commands')
