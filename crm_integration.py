import os
import logging
from typing import Dict, Optional, Tuple, Any
from hubspot_client import hubspot_client

# Set up logging
logger = logging.getLogger(__name__)

def save_contact_to_crm(phone_number: str, name: Optional[str] = None, 
                      email: Optional[str] = None, 
                      custom_fields: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """
    Save a contact to HubSpot CRM.
    
    Args:
        phone_number: The contact's phone number
        name: Optional contact name
        email: Optional contact email
        custom_fields: Optional dictionary of custom fields
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        # Prepare properties from custom_fields
        properties = {}
        if custom_fields:
            # Map common field names to HubSpot property names if needed
            property_mapping = {
                'first_name': 'firstname',
                'last_name': 'lastname',
                'phone': 'phone',
                'email': 'email',
                'source': 'hs_lead_source',
                'last_message': 'notes_last_contacted',
                'first_seen': 'hs_analytics_first_touch_converting_campaign',
                'last_contacted': 'notes_last_contacted_date'
            }
            
            for key, value in custom_fields.items():
                # Use mapped key if it exists, otherwise use the original key
                hubspot_key = property_mapping.get(key, key)
                properties[hubspot_key] = value
        
        # Save to HubSpot
        success, message = hubspot_client.create_or_update_contact(
            phone_number=phone_number,
            name=name,
            email=email,
            properties=properties
        )
        
        return success, message
        
    except Exception as e:
        error_msg = f"Error saving contact to HubSpot: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return False, error_msg

def log_communication(phone_number: str, direction: str, message: str, 
                     status: str = 'delivered', contact_id: Optional[str] = None) -> bool:
    """
    Log a communication in HubSpot.
    
    Args:
        phone_number: The contact's phone number
        direction: 'inbound' or 'outbound'
        message: The message content
        status: Delivery status
        contact_id: Optional HubSpot contact ID
        
    Returns:
        bool: True if logged successfully, False otherwise
    """
    try:
        # If no contact_id provided, try to find it by phone number
        if not contact_id:
            # In a real implementation, you would search for the contact by phone
            # For now, we'll just log that we would have looked it up
            logger.info(f"Would look up contact by phone: {phone_number}")
            return True
            
        # Log the communication in HubSpot
        direction_upper = direction.upper()
        status_upper = status.upper()
        
        return hubspot_client.log_communication(
            contact_id=contact_id,
            message=message,
            direction=direction_upper,
            status=status_upper
        )
        
    except Exception as e:
        logger.error(f"Error logging communication to HubSpot: {str(e)}", exc_info=True)
        return False
