"""
WebSocket handler for multiplayer game communication.
Uses Flask-SocketIO for real-time bidirectional communication.
"""

from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime
import redis_client

# SocketIO instance - will be initialized in server.py
socketio = None


def init_socketio(app):
    """Initialize SocketIO with the Flask app."""
    global socketio
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
    register_handlers()
    return socketio


def register_handlers():
    """Register all WebSocket event handlers."""

    @socketio.on('connect')
    def handle_connect():
        """Handle new WebSocket connection."""
        print(f"Client connected")
        emit('connected', {'status': 'ok'})

    @socketio.on('disconnect')
    def handle_disconnect():
        """Handle WebSocket disconnection."""
        print(f"Client disconnected")

    @socketio.on('join_game')
    def handle_join_game(data):
        """Player joins a multiplayer game room."""
        room_code = data.get('roomCode')
        player_id = data.get('playerId')
        player_name = data.get('playerName', 'Player')

        if not room_code or not player_id:
            emit('error', {'message': 'Missing roomCode or playerId'})
            return

        # Join the Socket.IO room
        join_room(room_code)

        # Notify others in the room
        emit('player_joined', {
            'playerId': player_id,
            'playerName': player_name,
            'timestamp': datetime.now().isoformat()
        }, to=room_code, include_self=False)

        # Confirm to the joining player
        emit('joined_game', {
            'roomCode': room_code,
            'success': True
        })

        print(f"Player {player_name} ({player_id}) joined room {room_code}")

    @socketio.on('leave_game')
    def handle_leave_game(data):
        """Player leaves a multiplayer game room."""
        room_code = data.get('roomCode')
        player_id = data.get('playerId')

        if room_code:
            leave_room(room_code)

            # Notify others
            emit('player_left', {
                'playerId': player_id,
                'timestamp': datetime.now().isoformat()
            }, to=room_code)

    @socketio.on('player_state')
    def handle_player_state(data):
        """Broadcast player position/state to room."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        # Broadcast to all other players in the room
        emit('game_update', {
            'type': 'player_state',
            'playerId': data.get('playerId'),
            'x': data.get('x'),
            'y': data.get('y'),
            'dx': data.get('dx', 0),
            'dy': data.get('dy', 0),
            'shieldActive': data.get('shieldActive', False),
            'ghostActive': data.get('ghostActive', False),
            'currentWeapon': data.get('currentWeapon', 'pistol'),
            'lives': data.get('lives', 3),
            'score': data.get('score', 0),
            'timestamp': datetime.now().isoformat()
        }, to=room_code, include_self=False)

    @socketio.on('player_shoot')
    def handle_player_shoot(data):
        """Player fired a bullet."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('game_update', {
            'type': 'shoot',
            'playerId': data.get('playerId'),
            'x': data.get('x'),
            'y': data.get('y'),
            'weapon': data.get('weapon', 'pistol'),
            'bulletId': data.get('bulletId'),
            'timestamp': datetime.now().isoformat()
        }, to=room_code, include_self=False)

    @socketio.on('player_hit')
    def handle_player_hit(data):
        """Player was hit by a bullet."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('game_update', {
            'type': 'hit',
            'targetId': data.get('targetId'),
            'shooterId': data.get('shooterId'),
            'damage': data.get('damage', 1),
            'remainingLives': data.get('remainingLives'),
            'timestamp': datetime.now().isoformat()
        }, to=room_code)

    @socketio.on('player_died')
    def handle_player_died(data):
        """Player died (lost all lives or a life in coop)."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('game_update', {
            'type': 'died',
            'playerId': data.get('playerId'),
            'reason': data.get('reason', 'unknown'),
            'respawnsLeft': data.get('respawnsLeft', 0),
            'timestamp': datetime.now().isoformat()
        }, to=room_code)

    @socketio.on('player_respawn')
    def handle_player_respawn(data):
        """Player respawned (coop mode)."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('game_update', {
            'type': 'respawn',
            'playerId': data.get('playerId'),
            'x': data.get('x'),
            'y': data.get('y'),
            'respawnsLeft': data.get('respawnsLeft'),
            'timestamp': datetime.now().isoformat()
        }, to=room_code)

    @socketio.on('send_hazard')
    def handle_send_hazard(data):
        """Player sends a hazard to opponent (versus mode)."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('receive_hazard', {
            'senderId': data.get('senderId'),
            'hazardType': data.get('hazardType'),
            'timestamp': datetime.now().isoformat()
        }, to=room_code, include_self=False)

    @socketio.on('game_event')
    def handle_game_event(data):
        """Generic game event (powerup collected, enemy killed, etc.)."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('game_update', {
            'type': 'event',
            'playerId': data.get('playerId'),
            'event': data.get('event'),
            'details': data.get('details', {}),
            'timestamp': datetime.now().isoformat()
        }, to=room_code, include_self=False)

    @socketio.on('spawn_enemy')
    def handle_spawn_enemy(data):
        """Synchronize enemy spawning in coop mode."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('game_update', {
            'type': 'spawn_enemy',
            'enemy': data.get('enemy'),
            'timestamp': datetime.now().isoformat()
        }, to=room_code, include_self=False)

    @socketio.on('spawn_boss')
    def handle_spawn_boss(data):
        """Synchronize boss spawning in coop mode."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('game_update', {
            'type': 'spawn_boss',
            'bossLevel': data.get('bossLevel'),
            'boss': data.get('boss'),
            'timestamp': datetime.now().isoformat()
        }, to=room_code, include_self=False)

    @socketio.on('boss_damage')
    def handle_boss_damage(data):
        """Synchronize boss damage in coop mode."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('game_update', {
            'type': 'boss_damage',
            'playerId': data.get('playerId'),
            'damage': data.get('damage'),
            'bossHealth': data.get('bossHealth'),
            'timestamp': datetime.now().isoformat()
        }, to=room_code, include_self=False)

    @socketio.on('boss_defeated')
    def handle_boss_defeated(data):
        """Boss was defeated in coop mode."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('game_update', {
            'type': 'boss_defeated',
            'bossLevel': data.get('bossLevel'),
            'defeatedBy': data.get('defeatedBy'),
            'timestamp': datetime.now().isoformat()
        }, to=room_code)

    @socketio.on('round_end')
    def handle_round_end(data):
        """Round ended in versus mode."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('game_update', {
            'type': 'round_end',
            'winnerId': data.get('winnerId'),
            'roundNumber': data.get('roundNumber'),
            'scores': data.get('scores'),
            'timestamp': datetime.now().isoformat()
        }, to=room_code)

    @socketio.on('match_end')
    def handle_match_end(data):
        """Match ended in versus mode."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        # Update Redis room state
        redis_client.end_room_game(room_code, data.get('winnerId'))

        emit('game_update', {
            'type': 'match_end',
            'winnerId': data.get('winnerId'),
            'finalScores': data.get('finalScores'),
            'timestamp': datetime.now().isoformat()
        }, to=room_code)

    @socketio.on('game_over')
    def handle_game_over(data):
        """Game over in coop mode."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        # Update Redis room state
        redis_client.end_room_game(room_code)

        emit('game_update', {
            'type': 'game_over',
            'reason': data.get('reason'),
            'finalScore': data.get('finalScore'),
            'level': data.get('level'),
            'timestamp': datetime.now().isoformat()
        }, to=room_code)

    @socketio.on('chat_message')
    def handle_chat_message(data):
        """Chat message in multiplayer lobby/game."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('chat', {
            'playerId': data.get('playerId'),
            'playerName': data.get('playerName'),
            'message': data.get('message', '')[:100],  # Limit message length
            'timestamp': datetime.now().isoformat()
        }, to=room_code)

    @socketio.on('ready_status')
    def handle_ready_status(data):
        """Player ready status changed in lobby."""
        room_code = data.get('roomCode')
        player_id = data.get('playerId')
        ready = data.get('ready', False)

        if not room_code or not player_id:
            return

        # Update Redis
        room = redis_client.set_player_ready(room_code, player_id, ready)

        # Broadcast to room
        emit('room_update', {
            'type': 'ready_status',
            'playerId': player_id,
            'ready': ready,
            'room': room
        }, to=room_code)

    @socketio.on('start_countdown')
    def handle_start_countdown(data):
        """Host started the game countdown."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        emit('game_update', {
            'type': 'countdown',
            'seconds': data.get('seconds', 3),
            'timestamp': datetime.now().isoformat()
        }, to=room_code)

    @socketio.on('game_started')
    def handle_game_started(data):
        """Game has started after countdown."""
        room_code = data.get('roomCode')

        if not room_code:
            return

        # Update Redis room status
        redis_client.start_room_game(room_code)

        emit('game_update', {
            'type': 'game_started',
            'mode': data.get('mode'),
            'difficulty': data.get('difficulty'),
            'timestamp': datetime.now().isoformat()
        }, to=room_code)
