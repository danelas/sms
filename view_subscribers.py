import sqlite3
import os
from tabulate import tabulate

def view_subscribers():
    """View all subscribers in the database."""
    db_path = os.path.join(os.path.dirname(__file__), 'sms_webhook.db')
    
    if not os.path.exists(db_path):
        print(f"Database file not found at: {db_path}")
        print("The application may not have created the database yet.")
        print("Try sending a test message to trigger database creation.")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row  # This enables column access by name
        cursor = conn.cursor()
        
        # Get subscribers
        cursor.execute('''
            SELECT 
                id, 
                phone_number, 
                is_active,
                created_at,
                last_message_sent,
                next_message_time
            FROM subscribers
            ORDER BY created_at DESC
        ''')
        
        subscribers = cursor.fetchall()
        
        if not subscribers:
            print("No subscribers found in the database.")
            return
            
        # Format the output
        headers = ['ID', 'Phone Number', 'Active', 'Created At', 'Last Msg Sent', 'Next Msg Time']
        rows = []
        
        for sub in subscribers:
            rows.append([
                sub['id'],
                sub['phone_number'],
                'Yes' if sub['is_active'] else 'No',
                sub['created_at'],
                sub['last_message_sent'] or 'Never',
                sub['next_message_time'] or 'Not scheduled'
            ])
        
        print("\nSubscribers:")
        print(tabulate(rows, headers=headers, tablefmt='grid'))
        
        # Show scheduled messages
        cursor.execute('''
            SELECT 
                m.id,
                s.phone_number,
                substr(m.message_text, 1, 30) || '...' as preview,
                m.scheduled_time,
                m.status,
                m.sent_at
            FROM scheduled_messages m
            JOIN subscribers s ON m.subscriber_id = s.id
            ORDER BY m.scheduled_time DESC
            LIMIT 10
        ''')
        
        messages = cursor.fetchall()
        
        if messages:
            print("\nUpcoming Scheduled Messages:")
            msg_headers = ['ID', 'Phone', 'Message Preview', 'Scheduled Time', 'Status', 'Sent At']
            msg_rows = []
            
            for msg in messages:
                msg_rows.append([
                    msg['id'],
                    msg['phone_number'],
                    msg['preview'],
                    msg['scheduled_time'],
                    msg['status'],
                    msg['sent_at'] or 'Pending'
                ])
            
            print(tabulate(msg_rows, headers=msg_headers, tablefmt='grid'))
        
    except sqlite3.Error as e:
        print(f"Error accessing database: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    view_subscribers()
