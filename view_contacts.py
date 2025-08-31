import sqlite3
import os
from tabulate import tabulate

def view_contacts():
    """View all contacts in the database."""
    db_path = os.path.join(os.path.dirname(__file__), 'sms_webhook.db')
    
    if not os.path.exists(db_path):
        print("Database file not found at:", db_path)
        return
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get list of tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        print("\nTables in database:")
        for table in tables:
            print(f"- {table[0]}")
        
        # Show subscribers table if it exists
        if any('subscribers' in table for table in tables):
            print("\nSubscribers:")
            cursor.execute("SELECT * FROM subscribers")
            columns = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
            print(tabulate(rows, headers=columns, tablefmt='grid'))
        
        # Show scheduled_messages if it exists
        if any('scheduled_messages' in table for table in tables):
            print("\nScheduled Messages:")
            cursor.execute("""
                SELECT m.id, s.phone_number, m.message_text, 
                       m.scheduled_time, m.status, m.sent_at
                FROM scheduled_messages m
                LEFT JOIN subscribers s ON m.subscriber_id = s.id
                ORDER BY m.scheduled_time DESC
                LIMIT 20
            """)
            columns = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
            print(tabulate(rows, headers=columns, tablefmt='grid'))
            
    except Exception as e:
        print(f"Error accessing database: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    view_contacts()
