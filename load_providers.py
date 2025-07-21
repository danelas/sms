import gspread
from google.oauth2.service_account import Credentials

# Path to your service account JSON key file
import os
SERVICE_ACCOUNT_FILE = os.getenv('GOOGLE_SHEETS_API', '../sms-openai-fastapi/service-account.json')  # Reads from env or defaults

# Define the scope for Sheets API (readonly is safest)
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Authenticate using the service account
credentials = Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=SCOPES
)

def load_providers(sheet_name='Massage Providers', worksheet_name=None):
    """
    Load provider list from Google Sheets as a list of dictionaries.
    :param sheet_name: Name of the Google Sheet document
    :param worksheet_name: Name of the worksheet/tab (default: first sheet)
    :return: List of provider dicts
    """
    gc = gspread.authorize(credentials)
    sh = gc.open(sheet_name)
    if worksheet_name:
        worksheet = sh.worksheet(worksheet_name)
    else:
        worksheet = sh.sheet1
    providers = worksheet.get_all_records()
    return providers

if __name__ == "__main__":
    # Example usage
    providers = load_providers()
    for provider in providers:
        print(provider)
