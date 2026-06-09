from app import app
import app_integrations
app_integrations._is_founder = lambda: True
client = app.test_client()
with client.session_transaction() as sess:
    sess['role'] = 'founder'
    
try:
    response = client.get('/founder/integrations/social', follow_redirects=False)
    print('Social Settings Status:', response.status_code)
except Exception as e:
    print('Social Settings Error:', e)

try:
    response2 = client.get('/founder/social/dashboard', follow_redirects=False)
    print('Social Dashboard Status:', response2.status_code)
except Exception as e:
    print('Social Dashboard Error:', e)
