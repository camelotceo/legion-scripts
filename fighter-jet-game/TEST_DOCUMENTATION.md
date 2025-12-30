# Fighter Jet Game - Test Suite Documentation

## Overview

This document details the unit test suite created for the Fighter Jet Game, with particular focus on testing the 1v1 head-to-head multiplayer functionality.

## Test Files Created

### 1. `conftest.py` - Pytest Configuration
- Flask test client fixture with temporary data directory
- FakeRedis fixture for mocking Redis operations
- Sample data fixtures (player, leaderboard, game state)
- Environment setup for testing mode
- Custom pytest markers (unit, integration, redis, database)

### 2. `pytest.ini` - Pytest Settings
- Test discovery patterns
- Logging configuration (INFO level to console)
- Strict markers mode
- Deprecation warning filters

### 3. `requirements-test.txt` - Test Dependencies
- pytest >= 7.0.0
- pytest-cov >= 4.0.0
- pytest-mock >= 3.0.0
- fakeredis >= 2.0.0

### 4. `tests/test_server.py` - API Endpoint Tests (21 tests)
Tests all Flask API endpoints:
- **TestLeaderboardAPI**: GET/POST leaderboard, score sorting
- **TestPlayerAPI**: Join, update, leave, active players
- **TestActionAPI**: Action reporting with emoji responses
- **TestSessionAPI**: Session start/end validation
- **TestSpectatorAPI**: Spectator comments, game state updates
- **TestVictoryAPI**: Victory saving
- **TestPlayerJourney**: Complete game flow integration test

### 5. `tests/test_redis_client.py` - Redis Operations Tests (29 tests)
Tests all Redis client operations:
- **TestPlayerState**: CRUD operations for player state
- **TestMatchmakingQueue**: Queue add/remove/find operations
- **TestRoomManagement**: Room create/join/leave/expire
- **TestSpectating**: Spectator add/remove/count
- **TestMultiplayerStateSync**: Game state storage/retrieval
- **TestRateLimiting**: Rate limit counter operations
- **TestConnection**: Redis ping and connection info

### 6. `tests/test_multiplayer.py` - 1v1 Feature Tests (29 tests)
Comprehensive tests for multiplayer functionality:
- **TestRoomManagement**: Room creation, joining, leaving, host transfer
- **TestPlayerReadyState**: Ready status, game start requirements
- **TestMatchmaking**: Queue operations, match finding
- **TestMultiplayerState**: State sync operations
- **TestVersusMode**: Versus-specific room and game flow
- **TestErrorHandling**: Edge cases and error conditions
- **TestKnownIssues**: Specific tests for documented 1v1 issues

## Running Tests

```bash
cd /Users/mrawesome/Desktop/dev/felicanai/web/test/fighter-jet-game

# Install dependencies
./venv/bin/pip install pytest pytest-mock fakeredis eventlet flask-socketio

# Run all tests
./venv/bin/python -m pytest tests/ -v

# Run only multiplayer tests
./venv/bin/python -m pytest tests/test_multiplayer.py -v

# Run only server API tests
./venv/bin/python -m pytest tests/test_server.py -v

# Run with coverage
./venv/bin/python -m pytest tests/ --cov=. --cov-report=html
```

## Test Results

**Latest Run:** 79 tests passed in 3.54 seconds

```
tests/test_multiplayer.py: 29 passed
tests/test_redis_client.py: 29 passed
tests/test_server.py: 21 passed
```

---

## 1v1 Head-to-Head Analysis

### Current State

Based on code analysis of `websocket_handler.py`, `redis_client.py`, `server.py`, and the HTML frontend, the 1v1 infrastructure is in place but has synchronization issues.

### Problem Summary

Players cannot see each other moving or firing, and hits don't register in versus mode.

### Root Cause Analysis

#### 1. **WebSocket Room Synchronization**
The `join_game` handler correctly calls `join_room(room_code)`, but there's no verification that both players successfully joined the same Socket.IO room before starting.

**Evidence:**
```python
# websocket_handler.py:48-62
join_room(room_code)
emit('player_joined', {...}, to=room_code, include_self=False)
emit('joined_game', {'roomCode': room_code, 'success': True})
```

**Issue:** No server-side tracking of which players are in which Socket.IO room. If one player's join fails, the server doesn't know.

#### 2. **Position Sync Broadcast**
The `player_state` handler broadcasts with `include_self=False`, which is correct, but the message is wrapped in a `game_update` event:

```python
# websocket_handler.py:96-114
emit('game_update', {
    'type': 'player_state',
    'playerId': data.get('playerId'),
    'x': data.get('x'),
    'y': data.get('y'),
    ...
}, to=room_code, include_self=False)
```

**Potential Issue:** The client must be listening for `game_update` events AND correctly parsing the `type: 'player_state'` to update opponent position. If the handler is named differently or the type check is wrong, positions won't sync.

#### 3. **Bullet/Shoot Event Handling**
Similarly, shooting is broadcast as `game_update` with `type: 'shoot'`:

```python
# websocket_handler.py:129-137
emit('game_update', {
    'type': 'shoot',
    'playerId': data.get('playerId'),
    'x': data.get('x'),
    'y': data.get('y'),
    ...
}, to=room_code, include_self=False)
```

**Potential Issue:** Client must handle this event AND create a bullet with `fromPartner: true` flag for collision detection to work.

#### 4. **Collision Detection Prerequisites**
According to CLAUDE.md, collision detection requires:
- `otherPlayer.id` to be set
- `versusState.roundActive` to be true
- Bullets must have correct flags (`!bullet.isEnemy && !bullet.fromPartner` for our bullets, `bullet.fromPartner` for opponent bullets)

**Potential Issue:** If `otherPlayer.id` is never set (because position sync isn't working), collision detection is disabled.

### Recommended Fixes

#### Fix 1: Add Room Membership Verification
Add server-side tracking of Socket.IO room membership:

```python
# In websocket_handler.py
room_members = {}  # room_code -> set of player_ids

@socketio.on('join_game')
def handle_join_game(data):
    room_code = data.get('roomCode')
    player_id = data.get('playerId')

    join_room(room_code)

    # Track membership
    if room_code not in room_members:
        room_members[room_code] = set()
    room_members[room_code].add(player_id)

    # Tell client who else is in the room
    emit('joined_game', {
        'roomCode': room_code,
        'success': True,
        'otherPlayers': list(room_members[room_code] - {player_id})
    })
```

#### Fix 2: Add Connection Health Check
Add periodic ping/pong to verify WebSocket connections:

```javascript
// Client-side
setInterval(() => {
    if (socket && socket.connected) {
        socket.emit('ping');
    }
}, 5000);

socket.on('pong', () => {
    lastPongTime = Date.now();
});
```

#### Fix 3: Add Debug Mode Toggle
Add a URL parameter to enable verbose logging:

```javascript
const DEBUG_PVP = new URLSearchParams(window.location.search).has('debug_pvp');

if (DEBUG_PVP) {
    console.log('PvP Debug: Received player_state', data);
}
```

#### Fix 4: Verify Client Event Handler Names
Ensure the client is listening for the correct events:

```javascript
// Must listen for 'game_update' not 'player_state'
socket.on('game_update', (data) => {
    if (data.type === 'player_state') {
        updateOtherPlayer(data);
    } else if (data.type === 'shoot') {
        handleOtherPlayerShoot(data);
    }
});
```

### Testing Recommendations

#### Manual Testing Steps
1. Open two browser tabs/windows
2. Enable debug mode: `?debug_pvp=1`
3. Create a room in tab 1, join with code in tab 2
4. Check browser console in both tabs for:
   - "Successfully joined room: XXXXXX"
   - "PvP: Sending position X Y to room XXXXXX"
   - "PvP: Received player_state from X at (x, y)"
5. Check server logs: `docker logs fighter-jet-game --tail 100`

#### Automated Testing
Run the multiplayer tests:
```bash
./venv/bin/python -m pytest tests/test_multiplayer.py -v
```

The `TestKnownIssues` class specifically tests:
- Position sync state structure
- Bullet state structure
- Room player tracking

---

## Files Modified

| File | Action | Purpose |
|------|--------|---------|
| `conftest.py` | Created | Pytest fixtures |
| `pytest.ini` | Created | Pytest configuration |
| `requirements-test.txt` | Created | Test dependencies |
| `tests/test_server.py` | Created | API endpoint tests |
| `tests/test_redis_client.py` | Created | Redis operation tests |
| `tests/test_multiplayer.py` | Created | 1v1 multiplayer tests |
| `TEST_DOCUMENTATION.md` | Created | This documentation |

## Conclusion

The test suite is now in place with 79 passing tests. The 1v1 issues appear to be related to client-side event handling rather than server-side infrastructure. The WebSocket handlers in `websocket_handler.py` are correctly implemented, but the client may not be:
1. Listening for the correct event names
2. Setting `otherPlayer.id` from received state
3. Creating opponent bullets with `fromPartner: true`

The recommended next step is to add the debug logging suggested above and trace the exact flow of position/shoot events between two connected clients.
