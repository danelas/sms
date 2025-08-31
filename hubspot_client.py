import os
import json
import logging
import requests
from typing import Dict, Optional, Tuple, Any

# Set up logging
logger = logging.getLogger(__name__)

class HubSpotClient:
    """Client for interacting with HubSpot's API"""
    
    def __init__(self):
        self.access_token = os.getenv('HUBSPOT_ACCESS_TOKEN')
        self.client_secret = os.getenv('HUBSPOT_CLIENT_SECRET')
        self.base_url = os.getenv('HUBSPOT_API_BASE_URL', 'https://api.hubapi.com')
        self.api_version = 'v3'
        
        if not self.access_token or not self.client_secret:
            logger.warning("HubSpot credentials not fully configured in environment variables")
    
    def _get_headers(self) -> Dict[str, str]:
        """Get the headers for API requests"""
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
    
    def create_or_update_contact(self, phone_number: str, name: Optional[str] = None, 
                               email: Optional[str] = None, 
                               properties: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        Create or update a contact in HubSpot with just a phone number
        
        Args:
            phone_number: The contact's phone number (required)
            name: Optional contact name (not required)
            email: Optional contact email (not required)
            properties: Additional contact properties
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self.access_token:
            return False, "HubSpot access token not configured"
        
        # Clean and validate phone number
        if not phone_number or not isinstance(phone_number, str) or len(phone_number.strip()) < 10:
            return False, "Invalid phone number"
            
        # Generate a unique email if none provided using phone number
        if not email or not isinstance(email, str) or '@' not in email:
            email = f"sms-{phone_number.strip('+').replace(' ', '')}@sms.goldtouchmobile.com"
            
        # Prepare basic contact data for HubSpot
        contact_data = {
            'properties': {
                'phone': phone_number.strip(),
                'email': email,
                'firstname': name.split(' ')[0] if name else 'SMS',
                'lastname': ' '.join(name.split(' ')[1:]) if name and ' ' in name else 'Contact',
                'hs_lead_status': 'SMS_LEAD',
                'lifecyclestage': 'subscriber',
                'lead_source': 'SMS Subscription',
                'hs_analytics_source': 'SMS',
                **(properties or {})
            }
        }
        
        try:
            # First, try to find an existing contact by phone or email
            search_url = f"{self.base_url}/crm/{self.api_version}/objects/contacts/search"
            headers = self._get_headers()
            
            # First try to find by phone number (exact match)
            search_payload = {
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "phone",
                                "operator": "EQ",
                                "value": phone_number.strip()
                            }
                        ]
                    }
                ],
                "properties": ["email", "phone", "firstname", "lastname", "hs_object_id"],
                "limit": 1
            }
            
            response = requests.post(
                search_url,
                headers=headers,
                json=search_payload,
                timeout=10
            )
            
            if response.status_code == 200 and response.json().get('results'):
                # Contact exists, update it with only the properties we want to change
                existing_contact = response.json()['results'][0]
                contact_id = existing_contact['id']
                update_url = f"{self.base_url}/crm/{self.api_version}/objects/contacts/{contact_id}"
                
                # Prepare update data - only include properties that are different
                update_data = {'properties': {}}
                
                # Only update name if we have a new name and the existing one is a default
                existing_name = f"{existing_contact.get('properties', {}).get('firstname', '')} {existing_contact.get('properties', {}).get('lastname', '')}".strip()
                if name and (not existing_name or existing_name in ['SMS Contact', 'SMS', 'Contact']):
                    update_data['properties']['firstname'] = name.split(' ')[0]
                    if ' ' in name:
                        update_data['properties']['lastname'] = ' '.join(name.split(' ')[1:])
                
                # Only update email if it's a generated one or missing
                existing_email = existing_contact.get('properties', {}).get('email', '')
                if '@sms.goldtouchmobile.com' in existing_email or not existing_email:
                    update_data['properties']['email'] = contact_data['properties']['email']
                
                # Add any additional properties that were passed in
                if properties:
                    for key, value in properties.items():
                        if key not in update_data['properties']:
                            update_data['properties'][key] = value
                
                # Only make the update if we have properties to update
                if update_data['properties']:
                    response = requests.patch(
                        update_url,
                        headers=headers,
                        json=update_data,
                        timeout=10
                    )
                
                if response.status_code == 200:
                    return True, f"Contact {contact_id} updated successfully"
                else:
                    error_msg = f"Failed to update contact: {response.status_code} - {response.text}"
                    logger.error(error_msg)
                    return False, error_msg
            else:
                # Create new contact
                create_url = f"{self.base_url}/crm/{self.api_version}/objects/contacts"
                
                response = requests.post(
                    create_url,
                    headers=headers,
                    json=contact_data,
                    timeout=10
                )
                
                if response.status_code == 201:
                    contact_id = response.json().get('id')
                    return True, f"Contact {contact_id} created successfully"
                else:
                    error_msg = f"Failed to create contact: {response.status_code} - {response.text}"
                    logger.error(error_msg)
                    return False, error_msg
                    
        except Exception as e:
            error_msg = f"Error in HubSpot API call: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return False, error_msg
    
    def log_communication(self, contact_id: str, message: str, direction: str = 'INBOUND', 
                         type: str = 'SMS', status: str = 'RECEIVED') -> bool:
        """
        Log a communication in HubSpot
        
        Args:
            contact_id: HubSpot contact ID
            message: The message content
            direction: 'INBOUND' or 'OUTBOUND'
            type: Type of communication (e.g., 'SMS', 'EMAIL')
            status: Status of the communication
            
        Returns:
            bool: True if logged successfully, False otherwise
        """
        try:
            url = f"{self.base_url}/crm/{self.api_version}/objects/communications"
            headers = self._get_headers()
            
            data = {
                'properties': {
                    'hs_timestamp': str(int(time.time() * 1000)),  # Current time in milliseconds
                    'hs_communication_type': type,
                    'hs_communication_direction': direction,
                    'hs_communication_status': status,
                    'hs_communication_body': message[:5000],  # Limit message length
                    'hs_communication_to': 'SMS' if direction == 'INBOUND' else contact_id,
                    'hs_communication_from': contact_id if direction == 'INBOUND' else 'SMS'
                },
                'associations': [
                    {
                        'to': {'id': contact_id},
                        'types': [{
                            'associationCategory': 'HUBSPOT_DEFINED',
                            'associationTypeId': '198'  # Contact to Communication association type ID
                        }]
                    }
                ]
            }
            
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=10
            )
            
            if response.status_code == 201:
                logger.info(f"Logged communication for contact {contact_id}")
                return True
            else:
                logger.error(f"Failed to log communication: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error logging communication: {str(e)}", exc_info=True)
            return False

# Create a singleton instance
hubspot_client = HubSpotClient()
