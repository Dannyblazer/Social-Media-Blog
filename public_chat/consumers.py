from channels.generic.websocket import AsyncJsonWebsocketConsumer
import json
from django.conf import settings
from users.utils import LazyAccountEncoder
from django.core.serializers.python import Serializer
from django.core.paginator import Paginator
from channels.db import database_sync_to_async
from django.utils import timezone
from chat.utils import *
from django.contrib.contenttypes.models import ContentType
from notification.utils import *
from chat.models import UnreadChatRoomMessages
from notification.constants import *
from notification.models import Notification
from chat.constants import *
from friend.models import FriendList, FriendRequest
from chat.models import PrivateChatRoom, RoomChatMessage
from public_chat.models import PublicChatRoom, PublicRoomChatMessage, PublicRoomChatMessage
from public_chat.constants import *

class PublicChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        """Called during socket handshaking"""
        await self.accept()
        self.room_id = None

    async def disconnect(self, close_code):
        # Leave room group
        try:
            if self.room_id != None:
                await self.leave_room(self.room_id)
        except Exception:
            pass

    # Receive message from WebSocket
    async def receive_json(self, content):
        command = content.get("command", None)
        try:
            if command == "send":
                if len(content["message"].lstrip()) == 0:
                    raise ClientError(422, "You can't send an empty message!")
                    # Send message to room group via send_message()
                await self.send_room(content["room_id"], content["message"])
            elif command == "join":
                await self.join_room(content["room"])
            elif command == "leave":
                await self.leave_room(content['room_id'])
            elif command == "get_room_chat_messages":
                await self.display_progress_bar(True)
                room = await get_room_or_error(content['room_id'])
                payload = await get_room_chat_messages(room, content['page_number'])
                if payload != None:
                    payload = json.loads(payload)
                    await self.send_messages_payload(payload['messages'], payload['new_page_number'])
                else:
                    raise ClientError(204, "Something went wrong retriving chatroom messages.")
                await self.display_progress_bar(False)
        except ClientError as e:
            await self.display_progress_bar(False)
            await self.handle_client_error(e)

    
    async def send_room(self, room_id, message):
        """Called by receive_json when someone messages a room"""
        if self.room_id != None:
            if str(room_id) != str(self.room_id):
                raise ClientError("ROOM ACCESS DENIED", "Room access denied")
            if not is_authenticated(self.scope['user']):
                raise ClientError("AUTH_ERROR", "You must be authenticated to chat")
        else:
            raise ClientError("ROOM_ACCESS_DENIED", "Room access denied.")
        
        room = await get_room_or_error(room_id)
        await create_public_room_chat_message(room, self.scope['user'], message)

        await self.channel_layer.group_send(
            room.group_name, 
            {
                "type": "chat.message",
                "profile_image": self.scope['user'].profile_image.url,
                "username": self.scope['user'].username,
                "user_id": self.scope['user'].id,
                'message': message,
            }
        )

    # Receive message from room group
    async def chat_message(self, event):
        message = event["message"]

        timestamp = calculate_timestamp(timezone.now())
        # Send message to WebSocket
        await self.send_json({
            'msg_type': MSG_TYPE_MESSAGE,
            "profile_image": event['profile_image'],
            "username": event['username'],
            "user_id": event['user_id'],
            "message": event['message'],
            "naturalday": timestamp,
        },)

    async def send_messages_payload(self, messages, new_page_number):
        """Sends a payload of messages to the UI"""
        await self.send_json({
            "messages_payload": "messages_payload",
            "messages": messages,
            "new_page_number": new_page_number,
        })
    
    async def join_room(self, room_id):
        """Called by receive_json when someone sent a JOIN command"""
        is_auth = is_authenticated(self.scope['user'])
        try:
            room = await get_room_or_error(room_id)
        except ClientError as e:
            await self.handle_client_error(e)

        # Add user from users list
        if is_auth:
            await connect_user(room, self.scope['user'])

        # store that we are in the room
        self.room_id = room_id
        # Send a join response to client socket
        await self.send_json({
            "join":"join",
            "room_id": room_id,
            "user": self.scope['user'].username,
        })
        # Add from the group
        await self.channel_layer.group_add(
            room.group_name, self.channel_name
        )
        # Payload sent to update connected users UI
        num_connected_users = await get_num_connected_users(room)
        await self.channel_layer.group_send(
            room.group_name,
            {
                "type": "connected_user_count",
                "connected_user_count": num_connected_users
            }
        )

    async def handle_client_error(self, e):
        """Called when clientError is raised. Send err to UI"""
        errorData = {}
        errorData['error'] = e.code
        if e.message:
            errorData['message'] = e.message
            await self.send_json(errorData)
        return

    
    async def leave_room(self, room_id):
        """Called by receive_json when someone sent a LEAVE command"""
        is_auth = is_authenticated(self.scope['user'])
        try:
            room = await get_room_or_error(room_id)
        except ClientError as e:
            await self.handle_client_error(e)

        # Remove user from users list
        if is_auth:
            await disconnect_user(room, self.scope['user'])

        # Remove from the group
        await self.channel_layer.group_discard(
            room.group_name, self.channel_name
        )
        # Payload sent when a user leaves the group
        num_connected_users = await get_num_connected_users(room)
        await self.channel_layer.group_send(
            room.group_name,
            {
                "type": "connected_user_count",
                "connected_user_count": num_connected_users
            }
        )
        print("Someone left: " + str(num_connected_users))

    async def display_progress_bar(self, is_displayed):
        """Payload sent whenever there's a message payload being sent"""
        await self.send_json({
            "display_progress_bar": is_displayed
        })

    async def connected_user_count(self, event):
        """Called to send the number of connected users to the group chat"""
        await self.send_json({
            "msg_type": MSG_TYPE_CONNECTED_USER_COUNT,
            "connected_user_count": event['connected_user_count']
        })

@database_sync_to_async
def get_num_connected_users(room):
    if room.users:
        return len(room.users.all())
    else:
        return 0
        
def is_authenticated(user):
    if user.is_authenticated:
        return True
    return False

@database_sync_to_async
def connect_user(room, user):
    return room.connect_user(user)

@database_sync_to_async
def disconnect_user(room, user):
    return room.disconnect_user(user)

@database_sync_to_async
def create_public_room_chat_message(room, user, message):
    return PublicRoomChatMessage.objects.create(room=room, user=user, content=message)

@database_sync_to_async
def get_room_or_error(room_id):
    """ Tried to fetch a room for the user"""
    try:
        room = PublicChatRoom.objects.get(pk=room_id)
    except PublicChatRoom.DoesNotExist:
        raise ClientError("ROOM_INVALID", "Invalid room")
    return room

class ClientError(Exception):
    """
    Custom exception class that is caught by the webscoket receive()
    handler and translated into a send back to the client
    """
    def __init__(self, code, message):
        super().__init__(code)
        self.code = code
        if message:
            self.message = message


@database_sync_to_async
def get_room_chat_messages(room, page_number):
    try:
        qs = PublicRoomChatMessage.objects.filter(room=room).order_by("-timestamp")
        p = Paginator(qs, DEFAULT_ROOM_CHAT_MESSAGE_PAGE_SIZE)
        
        payload = {}
        new_page_number =int(page_number)
        if new_page_number <= p.num_pages:
            new_page_number = new_page_number + 1
            s = LazyRoomChatMessageEncoder()
            payload['messages'] = s.serialize(p.page(page_number).object_list)
        else:
            payload['messages'] = "None"
        payload['new_page_number'] = new_page_number
        return json.dumps(payload)    
    except Exception as e:
        print("EXCEPTION bottom: " + str(e))
        return None

class LazyRoomChatMessageEncoder(Serializer):
    def get_dump_object(self, obj):
        dump_object = {}
        dump_object.update({'msg_type': MSG_TYPE_MESSAGE})
        dump_object.update({'user_id': str(obj.user.id)})
        dump_object.update({'msg_id': str(obj.id)})
        dump_object.update({'message': str(obj.content)})
        dump_object.update({'username': str(obj.user.username)})
        dump_object.update({'profile_image': str(obj.user.profile_image.url)})
        dump_object.update({'naturalday': str(calculate_timestamp(obj.timestamp))})
        return dump_object






# Private Chat Room consumer


class ChatConsumer(AsyncJsonWebsocketConsumer):

	async def connect(self):
		"""
		Called when the websocket is handshaking as part of initial connection.
		"""
		print("ChatConsumer: connect: " + str(self.scope["user"].username))

		# let everyone connect. But limit read/write to authenticated users
		await self.accept()

		# the room_id will define what it means to be "connected". If it is not None, then the user is connected.
		self.room_id = None


	async def receive_json(self, content):
		"""
		Called when we get a text frame. Channels will JSON-decode the payload
		for us and pass it as the first argument.
		"""
		# Messages will have a "command" key we can switch on
		print("ChatConsumer: receive_json")
		command = content.get("command", None)
		try:
			if command == "join":
				await self.join_room(content['room'])
			elif command == "leave":
				# Leave the room
				await self.leave_room(content['room'])
			elif command == "send":
				if len(content['message'].lstrip()) != 0:
					await self.send_room(content['room'], content['message'])
			elif command == "get_room_chat_messages":
				await self.display_progress_bar(True)
				room = await get_room_or_error2(content['room_id'], self.scope['user'])
				payload = await get_room_chat_messages2(room, content['page_number'])
				if payload != None:
					payload = json.loads(payload)
					await self.send_messages_payload(payload['messages'], payload['new_page_number'])
				else:
					raise Exception("Something went wrong retrieving the chatroom messages.")
				await self.display_progress_bar(False)
			elif command == "get_user_info":
				await self.display_progress_bar(True)
				room = await get_room_or_error2(content['room_id'], self.scope["user"])
				payload = get_user_info(room, self.scope["user"])
				if payload != None:
					payload = json.loads(payload)
					await self.send_user_info_payload(payload['user_info'])
				else:
					raise Exception("Something went wrong retrieving the other users account details.")
		except Exception as e:
			pass


	async def disconnect(self, code):
		"""
		Called when the WebSocket closes for any reason.
		"""
		# Leave the room
		print("ChatConsumer: disconnect")
		try:
			if self.room_id != None:
				await self.leave_room(self.room_id)
		except Exception as e:
			pass

	async def join_room(self, room_id):
		"""
		Called by receive_json when someone sent a join command.
		"""
		# The logged-in user is in our scope thanks to the authentication ASGI middleware (AuthMiddlewareStack)
		print("ChatConsumer: join_room: " + str(room_id))
		try:
			room = await get_room_or_error2(room_id, self.scope['user'])
		except Exception as e:
			return
        
        #store that we're in the room
		self.room_id = room.id
        # Add them to the group, so that they get room messages
		await self.channel_layer.group_add(
            room.group_name,
            self.channel_name
        )

		await self.send_json({
            "join": str(room.id)
        })
		

		# Add them to the group so they get room messages

		# Instruct their client to finish opening the room

	async def leave_room(self, room_id):
		"""
		Called by receive_json when someone sent a leave command.
		"""
		# The logged-in user is in our scope thanks to the authentication ASGI middleware
		print("ChatConsumer: leave_room")
		
		room = await get_room_or_error2(room_id, self.scope['user'])

		# Notify the group that someone left
		await self.channel_layer.group_send(
            room.group_name,
            {
                "type": "chat.leave", # chat_leave function
                "room_id": room_id,
                "profile_image": self.scope['user'].profile_image.url,
                "username": self.scope['user'].username,
                "user_id": self.scope['user'].id,
            }
        )

		# Remove that we're in the room
		self.room_id = None

		# Remove them from the group so they no longer get room messages
		await self.channel_layer.group_discard(
            room.group_name,
            self.channel_name
        )
        
		# Instruct their client to finish closing the room
		


	async def send_room(self, room_id, message):
		"""
		Called by receive_json when someone sends a message to a room.
		"""
		print("ChatConsumer: send_room")
		# Check they are in this room
		if self.room_id != None:
			if str(room_id) != str(self.room_id):
				print("CLIENT ERRROR 1")
				raise ClientError("ROOM_ACCESS_DENIED", "Room access denied")
		else:
			print("CLIENT ERRROR 2")
			raise ClientError("ROOM_ACCESS_DENIED", "Room access denied")
		room = await get_room_or_error2(room_id, self.scope['user'])

		await create_room_chat_message(room, self.scope['user'], message)
		await self.channel_layer.group_send(
            room.group_name,
            {
                "type": "chat.message", # chat_message async function
                "profile_image": self.scope['user'].profile_image.url,
                "username": self.scope['user'].username,
                "user_id": self.scope['user'].id,
                "message": message,
            }
        )




	# These helper methods are named by the types we send - so chat.join becomes chat_join
	async def chat_join(self, event):
		"""
		Called when someone has joined our chat.
		"""
		# Send a message down to the client
		print("ChatConsumer: chat_join: " + str(self.scope["user"].id))
		if event["username"]:
			await self.send_json(
				{
					"msg_type": MSG_TYPE_ENTER2,
					"room": event["room_id"],
					"profile_image": event["profile_image"],
					"username": event["username"],
					"user_id": event["user_id"],
					"message": event["username"] + " connected.",
				},
			)

	async def chat_leave(self, event):
		"""
		Called when someone has left our chat.
		"""
		# Send a message down to the client
		pass


	async def chat_message(self, event):
		"""
		Called when someone has messaged our chat.
		"""
		# Send a message down to the client

		timestamp = calculate_timestamp(timezone.now())
		
		await self.send_json({
            "msg_type": MSG_TYPE_MESSAGE,
            "username": event['username'],
            "user_id": event['user_id'],
            "profile_image": event['profile_image'],
            "message": event['message'],
            "natural_timestamp": timestamp,

        })
    
	async def send_messages_payload(self, messages, new_page_number):
		"""
 		Send a payload of messages to the client sockets"""        
		print("ChatConsumer: send_messages_payload. ")
		await self.send_json({
			"messages_payload": "messages_payload",
			"messages": messages,
            "new_page_number": new_page_number,
		})
		

	async def send_user_info_payload(self, user_info):
		"""
		Send a payload of user information to the ui
		"""
		# print("ChatConsumer: send_user_info_payload. ")
		await self.send_json(
			{
				"user_info": user_info,
			},
		)

	async def display_progress_bar(self, is_displayed):
		"""
		1. is_displayed = True
		- Display the progress bar on UI
		2. is_displayed = False
		- Hide the progress bar on UI
		"""
		await self.send_json(
			{
				"display_progress_bar": is_displayed
			}
		)


	async def handle_client_error(self, e):
		"""
		Called when a ClientError is raised.
		Sends error data to UI.
		"""
		errorData = {}
		errorData['error'] = e.code
		if e.message:
			errorData['message'] = e.message
			await self.send_json(errorData)
		return

@database_sync_to_async
def get_room_or_error2(room_id, user):
    """
     Tries to fetch a room for the user, checking permissions along the way
    """
    try:
        room = PrivateChatRoom.objects.get(pk=room_id)
    except PrivateChatRoom.DoesNotExist:
        raise Exception("Invalid Room.")

    # Is this user allowed into this room?
    if user != room.user1 and user != room.user2:
        raise Exception("You do not have permission to join this room.")

    # Are the users in the room friend?
    friend_list = FriendList.objects.get(user=user).friends.all()
    if not room.user1 in friend_list:
        if not room.user2 in friend_list:
             raise Exception("You must be friends to chat.")
    return room

import json

def get_user_info(room, user):
    """
    Retrieve the user info for the user you're chatting with.
    """
    try:
        # Determine who is who
        other_user = room.user1 if room.user1 != user else room.user2
        s = LazyAccountEncoder()
        final = s.serialize([other_user])[0]
        payload = {'user_info': final}
        # Using dumps() instead of dump() to return JSON as a string
        return json.dumps(payload, indent=4, sort_keys=True, default=str)

    except Exception as e:
        # print("EXCEPTION: " + str(e))
        return None

    
@database_sync_to_async
def create_room_chat_message(room, user, message):
     return RoomChatMessage.objects.create(user=user, room=room, content=message)

@database_sync_to_async
def get_room_chat_messages2(room, page_number):
    try:
        qs = RoomChatMessage.objects.by_room(room)
        p = Paginator(qs, DEFAULT_ROOM_CHAT_MESSAGE_PAGE_SIZE1)

        payload = {}
        new_page_number = int(page_number)
        if new_page_number <= p.num_pages:
            new_page_number = new_page_number + 1
            s = LaxyRoomChatMessageEncoder2()
            payload['messages'] = s.serialize(p.page(page_number).object_list)
        else:
            payload['messages'] = None
        payload['new_page_number'] = new_page_number
        return json.dumps(payload)
    except Exception as e:
        print("EXCEPTION last: " + str(e))
    return None



# NOTIFICATIONS CONSUMER
"""
    The notification consumer is embedded into the public chat consumer as the private chat consumer too.
    Will fix this approach later
    """

class NotificationConsumer(AsyncJsonWebsocketConsumer):
	"""
	Passing data to and from header.html. Notifications are displayed as "drop-downs" in the nav bar.
	There is two major categories of notifications:
		1. General Notifications
			1. FriendRequest
			2. FriendList
		1. Chat Notifications
			1. UnreadChatRoomMessages
	"""

	async def connect(self):
		"""
		Called when the websocket is handshaking as part of initial connection.
		"""
		# print("NotificationConsumer: connect: " + str(self.scope["user"]) )
		await self.accept()


	async def disconnect(self, code):
		"""
		Called when the WebSocket closes for any reason.
		"""
		print("NotificationConsumer: disconnect")


	async def receive_json(self, content):
		"""
		Called when we get a text frame. Channels will JSON-decode the payload
		for us and pass it as the first argument.
		"""
		command = content.get("command", None)
		# print("NotificationConsumer: receive_json. Command: " + command)
		try:
			if command == "get_general_notifications":
				payload1 = await get_general_notifications(self.scope["user"], content.get("page_number", None))
				if payload1 == None:
					await self.general_pagination_exhausted()
				else:
					payload = json.loads(payload1)
					await self.send_general_notifications_payload(payload['notifications'], payload['new_page_number'])
			elif command == "get_new_general_notifications":
				payload = await get_new_general_notifications(self.scope["user"], content.get("newest_timestamp", None))
				if payload != None:
					payload = json.loads(payload)
					await self.send_new_general_notifications_payload(payload['notifications'])
			elif command == "accept_friend_request":
				notification_id = content['notification_id']
				payload = await accept_friend_request(self.scope['user'], notification_id)
				if payload == None:
					raise ClientError("UNKNOWN_ERROR", "Something went wrong. Try refreshing the browser.")
				else:
					payload = json.loads(payload)
					await self.send_updated_friend_request_notification(payload['notification'])
			elif command == "decline_friend_request":
				notification_id = content['notification_id']
				payload = await decline_friend_request(self.scope['user'], notification_id)
				if payload == None:
					raise ClientError("UNKNOWN_ERROR", "Something went wrong. Try refreshing the browser.")
				else:
					payload = json.loads(payload)
					await self.send_updated_friend_request_notification(payload['notification'])
			elif command == "refresh_general_notifications":
				payload = await refresh_general_notifications(self.scope["user"], content['oldest_timestamp'], content['newest_timestamp'])
				if payload == None:
					raise ClientError("UNKNOWN_ERROR", "Something went wrong. Try refreshing the browser.")
				else:
					payload = json.loads(payload)
					await self.send_general_refreshed_notifications_payload(payload['notifications'])
			elif command == "get_unread_general_notifications_count":
				payload = await get_unread_general_notification_count(self.scope["user"])
				if payload != None:
					payload = json.loads(payload)
					await self.send_unread_general_notification_count(payload['count'])
			elif command == "mark_notifications_read":
				await mark_notifications_read(self.scope["user"])

			elif command == "get_chat_notifications":
				payload = await get_chat_notifications(self.scope["user"], content.get("page_number", None))
				if payload == None:
					await self.chat_pagination_exhausted()
				else:
					payload = json.loads(payload)
					await self.send_chat_notifications_payload(payload['notifications'], payload['new_page_number'])
		
			elif command == "get_new_chat_notifications":
				payload = await get_new_chat_notifications(self.scope["user"], content.get("newest_timestamp", None))
				if payload != None:
					payload = json.loads(payload)
					await self.send_new_chat_notifications_payload(payload['notifications'])
			elif command == "get_unread_chat_notifications_count":
				try:
					payload = await get_unread_chat_notification_count(self.scope["user"])
					if payload != None:
						payload = json.loads(payload)
						await self.send_unread_chat_notification_count(payload['count'])
				except Exception as e:
					print("UNREAD CHAT MESSAGE COUNT EXCEPTION: " + str(e))
					pass
		except ClientError as e:
			print("EXCEPTION: receive_json: " + str(e))
			pass

	async def display_progress_bar(self, shouldDisplay):
		# print("NotificationConsumer: display_progress_bar: " + str(shouldDisplay)) 
		await self.send_json(
			{
				"progress_bar": shouldDisplay,
			},
		)

	async def send_general_notifications_payload(self, notifications, new_page_number):
		"""
		Called by receive_json when ready to send a json array of the notifications
		"""
		#print("NotificationConsumer: send_general_notifications_payload")
		await self.send_json(
			{
				"general_msg_type": GENERAL_MSG_TYPE_NOTIFICATIONS_PAYLOAD,
				"notifications": notifications,
				"new_page_number": new_page_number,
			},
		)

	async def send_updated_friend_request_notification(self, notification):
		"""
		After a friend request is accepted or declined, send the updated notification to template
		payload contains 'notification' and 'response':
		1. payload['notification']
		2. payload['response']
		"""
		await self.send_json(
			{
				"general_msg_type": GENERAL_MSG_TYPE_UPDATED_NOTIFICATION,
				"notification": notification,
			},
		)

	async def send_updated_friend_request_notification(self, notification):
		"""
		After a friend request is accepted or declined, send the updated notification to template
		payload contains 'notification' and 'response':
		1. payload['notification']
		2. payload['response']
		"""
		await self.send_json(
			{
				"general_msg_type": GENERAL_MSG_TYPE_UPDATED_NOTIFICATION,
				"notification": notification,
			},
		)

	async def general_pagination_exhausted(self):
		"""
		Called by receive_json when pagination is exhausted for general notifications
		"""
		#print("General Pagination DONE... No more notifications.")
		await self.send_json(
			{
				"general_msg_type": GENERAL_MSG_TYPE_PAGINATION_EXHAUSTED,
			},
		)

	async def send_general_refreshed_notifications_payload(self, notifications):
		"""
		Called by receive_json when ready to send a json array of the notifications
		"""
		#print("NotificationConsumer: send_general_refreshed_notifications_payload: " + str(notifications))
		await self.send_json(
			{
				"general_msg_type": GENERAL_MSG_TYPE_NOTIFICATIONS_REFRESH_PAYLOAD,
				"notifications": notifications,
			},
		)

	async def send_new_general_notifications_payload(self, notifications):
		"""
		Called by receive_json when ready to send a json array of the notifications
		"""
		await self.send_json(
			{
				"general_msg_type": GENERAL_MSG_TYPE_GET_NEW_GENERAL_NOTIFICATIONS,
				"notifications": notifications,
			},
		)

	async def send_unread_general_notification_count(self, count):
		"""
		Send the number of unread "general" notifications to the template
		"""
		await self.send_json(
			{
				"general_msg_type": GENERAL_MSG_TYPE_GET_UNREAD_NOTIFICATIONS_COUNT,
				"count": count,
			},
		)

	async def send_chat_notifications_payload(self, notifications, new_page_number):
		"""
		Called by receive_json when ready to send a json array of the chat notifications
		"""
		#print("NotificationConsumer: send_chat_notifications_payload")
		await self.send_json(
			{
				"chat_msg_type": CHAT_MSG_TYPE_NOTIFICATIONS_PAYLOAD,
				"notifications": notifications,
				"new_page_number": new_page_number,
			},
		)

	async def send_new_chat_notifications_payload(self, notifications):
		"""
		Called by receive_json when ready to send a json array of the notifications
		"""
		await self.send_json(
			{
				"chat_msg_type": CHAT_MSG_TYPE_GET_NEW_NOTIFICATIONS,
				"notifications": notifications,
			},
		)

	async def chat_pagination_exhausted(self):
		"""
		Called by receive_json when pagination is exhausted for chat notifications
		"""
		# print("Chat Pagination DONE... No more notifications.")
		await self.send_json(
			{
				"chat_msg_type": CHAT_MSG_TYPE_PAGINATION_EXHAUSTED,
			},
		)

	async def send_unread_chat_notification_count(self, count):
		"""
		Send the number of unread "chat" notifications to the template
		"""
		await self.send_json(
			{
				"chat_msg_type": CHAT_MSG_TYPE_GET_UNREAD_NOTIFICATIONS_COUNT,
				"count": count,
			},
		)



@database_sync_to_async
def get_general_notifications(user, page_number):
	"""
	Get General Notifications with Pagination (next page of results).
	This is for appending to the bottom of the notifications list.
	General Notifications are:
	1. FriendRequest
	2. FriendList
	"""
	if user.is_authenticated:
		friend_request_ct = ContentType.objects.get_for_model(FriendRequest)
		friend_list_ct = ContentType.objects.get_for_model(FriendList)
		notifications = Notification.objects.filter(target=user, content_type__in=[friend_request_ct, friend_list_ct]).order_by('-timestamp')
		p = Paginator(notifications, DEFAULT_NOTIFICATION_PAGE_SIZE)

		payload = {}
		if len(notifications) > 0:
			if int(page_number) <= p.num_pages:
				s = LazyNotificationEncoder()
				serialized_notifications = s.serialize(p.page(page_number).object_list)
				payload['notifications'] = serialized_notifications
				new_page_number = int(page_number) + 1
				payload['new_page_number'] = new_page_number
		else:
			return None
	else:
		raise ClientError("AUTH_ERROR", "User must be authenticated to get notifications.")

	return json.dumps(payload)


@database_sync_to_async
def accept_friend_request(user, notification_id):
    """
    Accept a friend request
    """
    payload = {}
    if user.is_authenticated:
        try:
            notification = Notification.objects.get(pk=notification_id)
            friend_request = notification.content_object
            # confirm this is the correct user
            if friend_request.receiver == user:
                # accept the request and get the updated notification
                updated_notification = friend_request.accept()

                # return the notification associated with this FriendRequest
                s = LazyNotificationEncoder()
                payload['notification'] = s.serialize([updated_notification])[0]
                return json.dumps(payload)
        except Notification.DoesNotExist:
            raise ClientError("AUTH_ERROR", "An error occurred with that notification. Try refreshing the browser.")
    return None


@database_sync_to_async
def decline_friend_request(user, notification_id):
	"""
	Decline a friend request
	"""
	payload = {}
	if user.is_authenticated:
		try:
			notification = Notification.objects.get(pk=notification_id)
			friend_request = notification.content_object
			# confirm this is the correct user
			if friend_request.receiver == user:
				# accept the request and get the updated notification
				updated_notification = friend_request.decline()

				# return the notification associated with this FriendRequest
				s = LazyNotificationEncoder()
				payload['notification'] = s.serialize([updated_notification])[0]
				return json.dumps(payload)
		except Notification.DoesNotExist:
			raise ClientError("AUTH_ERROR", "An error occurred with that notification. Try refreshing the browser.")
	return None



@database_sync_to_async
def refresh_general_notifications(user, oldest_timestamp, newest_timestamp):
	"""
	Retrieve the general notifications newer than the oldest one on the screen and younger than the newest one the screen.
	The result will be: Notifications currently visible will be updated
	"""
	payload = {}
	if user.is_authenticated:
		oldest_ts = oldest_timestamp[0:oldest_timestamp.find("+")] # remove timezone because who cares
		oldest_ts = datetime.strptime(oldest_ts, '%Y-%m-%d %H:%M:%S.%f')
		newest_ts = newest_timestamp[0:newest_timestamp.find("+")] # remove timezone because who cares
		newest_ts = datetime.strptime(newest_ts, '%Y-%m-%d %H:%M:%S.%f')
		friend_request_ct = ContentType.objects.get_for_model(FriendRequest)
		friend_list_ct = ContentType.objects.get_for_model(FriendList)
		notifications = Notification.objects.filter(target=user, content_type__in=[friend_request_ct, friend_list_ct], timestamp__gte=oldest_ts, timestamp__lte=newest_ts).order_by('-timestamp')

		s = LazyNotificationEncoder()
		payload['notifications'] = s.serialize(notifications)
	else:
		raise ClientError("AUTH_ERROR", "User must be authenticated to get notifications.")

	return json.dumps(payload) 


@database_sync_to_async
def get_new_general_notifications(user, newest_timestamp):
	"""
	Retrieve any notifications newer than the newest_timestatmp on the screen.
	"""
	payload = {}
	if user.is_authenticated:
		timestamp = newest_timestamp[0:newest_timestamp.find("+")] # remove timezone because who cares
		timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S.%f')
		friend_request_ct = ContentType.objects.get_for_model(FriendRequest)
		friend_list_ct = ContentType.objects.get_for_model(FriendList)
		notifications = Notification.objects.filter(target=user, content_type__in=[friend_request_ct, friend_list_ct], timestamp__gt=timestamp, read=False).order_by('-timestamp')
		s = LazyNotificationEncoder()
		payload['notifications'] = s.serialize(notifications)
	else:
		raise ClientError("AUTH_ERROR", "User must be authenticated to get notifications.")

	return json.dumps(payload) 



@database_sync_to_async
def get_unread_general_notification_count(user):
	payload = {}
	if user.is_authenticated:
		friend_request_ct = ContentType.objects.get_for_model(FriendRequest)
		friend_list_ct = ContentType.objects.get_for_model(FriendList)
		notifications = Notification.objects.filter(target=user, content_type__in=[friend_request_ct, friend_list_ct])

		unread_count = 0
		if notifications:
			for notification in notifications.all():
				if not notification.read:
					unread_count = unread_count + 1
		payload['count'] = unread_count
		return json.dumps(payload)
	else:
		raise ClientError("AUTH_ERROR", "User must be authenticated to get notifications.")
	return None



@database_sync_to_async
def mark_notifications_read(user):
	"""
	marks a notification as "read"
	"""
	if user.is_authenticated:
		notifications = Notification.objects.filter(target=user)
		if notifications:
			for notification in notifications.all():
				notification.read = True
				notification.save()
	return



@database_sync_to_async
def get_chat_notifications(user, page_number):
	"""
	Get Chat Notifications with Pagination (next page of results).
	This is for appending to the bottom of the notifications list.
	Chat Notifications are:
	1. UnreadChatRoomMessages
	"""
	if user.is_authenticated:
		chatmessage_ct = ContentType.objects.get_for_model(UnreadChatRoomMessages)
		notifications = Notification.objects.filter(target=user, content_type=chatmessage_ct).order_by('-timestamp')
		p = Paginator(notifications, DEFAULT_NOTIFICATION_PAGE_SIZE)

		# sleep 1s for testing
		# sleep(1)  
		# print("PAGES: " + str(p.num_pages))
		payload = {}
		if len(notifications) > 0:
			if int(page_number) <= p.num_pages:
				s = LazyNotificationEncoder()
				serialized_notifications = s.serialize(p.page(page_number).object_list)
				payload['notifications'] = serialized_notifications
				new_page_number = int(page_number) + 1
				payload['new_page_number'] = new_page_number
				return json.dumps(payload)
		else:
			return None
	else:
		raise ClientError("AUTH_ERROR", "User must be authenticated to get notifications.")
	return None
	


@database_sync_to_async
def get_new_chat_notifications(user, newest_timestatmp):
	"""
	Retrieve any notifications newer than the newest_timestatmp on the screen.
	"""
	payload = {}
	if user.is_authenticated:
		timestamp = newest_timestatmp[0:newest_timestatmp.find("+")] # remove timezone because who cares
		timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S.%f')
		chatmessage_ct = ContentType.objects.get_for_model(UnreadChatRoomMessages)
		notifications = Notification.objects.filter(target=user, content_type__in=[chatmessage_ct], timestamp__gt=timestamp).order_by('-timestamp')
		s = LazyNotificationEncoder()
		payload['notifications'] = s.serialize(notifications)
		return json.dumps(payload) 
	else:
		raise ClientError("AUTH_ERROR", "User must be authenticated to get notifications.")

	return None


@database_sync_to_async
def get_unread_chat_notification_count(user):
    payload = {}
    if user.is_authenticated:
        chatmessage_ct = ContentType.objects.get_for_model(UnreadChatRoomMessages)
        notifications = Notification.objects.filter(target=user, content_type__in=[chatmessage_ct])
        
        unread_count = 0
        if notifications:
            unread_count = len(notifications.all())
        payload['count'] = unread_count
        return json.dumps(payload)
    else:
        raise ClientError("AUTH_ERROR", "User must be authenticated to get notifications.")

