import os
import gspread
from google.oauth2.service_account import Credentials

SERVICE_ACCOUNT_FILE = os.getenv('GOOGLE_SHEETS_API', '../sms-openai-fastapi/service-account.json')
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

credentials = Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=SCOPES
)

gc = gspread.authorize(credentials)

# List all spreadsheets accessible to the service account
def list_spreadsheets():
    files = gc.list_spreadsheet_files()
    for f in files:
        print(f"Title: {f['name']} | ID: {f['id']}")

if __name__ == "__main__":
    list_spreadsheets()
