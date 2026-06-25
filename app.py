from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import time
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-here')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ---------------------------
# 1. ИГРОВЫЕ КОНСТАНТЫ
# ---------------------------

VALUES = {"Яхта": 200, "Дом": 100, "Мебель": 40, "Велосипед": -20}
EMOJIS = {"Яхта": "⛵", "Дом": "🏠", "Мебель": "🪑", "Велосипед": "🚲"}

MAX_ROUNDS = 3
MIN_CONTAINERS = 2
AUCTION_STEP = 10
AUCTION_MAX_RAISES = 3
ROUND_TIMEOUT = 30

# ---------------------------
# 2. УПРАВЛЕНИЕ ИГРАМИ
# ---------------------------

games = {}  # {game_id: game_data}
game_players = {}  # {player_sid: game_id}

class ContainerGame:
    def __init__(self):
        self.reset()
    
    def reset(self):
        self.pool = self.shuffle_pool()
        self.current_round = 0
        self.players = {
            'player1': {'name': 'Игрок 1', 'chips': 150, 'containers': [], 'score': 0, 
                       'used_xray': False, 'used_intercept': False, 'sid': None},
            'player2': {'name': 'Игрок 2', 'chips': 150, 'containers': [], 'score': 0,
                       'used_xray': False, 'used_intercept': False, 'sid': None}
        }
        self.containers = []
        self.auction_active = False
        self.auction_data = {}
        self.game_over = False
        self.winner = None
        self.message = "Добро пожаловать в игру!"
        self.round_start_time = time.time()
        self.started = False
        self.players_connected = {'player1': False, 'player2': False}
        
    def shuffle_pool(self):
        pool = ["Яхта"] * 2 + ["Дом"] * 3 + ["Мебель"] * 5 + ["Велосипед"] * 2
        random.shuffle(pool)
        return pool
    
    def generate_containers(self):
        if not self.pool:
            return []
        
        count = random.randint(1, min(5, len(self.pool)))
        containers = []
        
        for _ in range(count):
            if not self.pool:
                break
            
            c_type = self.pool.pop()
            price = random.randint(5, 65)
            
            containers.append({
                'id': len(containers),
                'type': c_type,
                'price': price,
                'value': VALUES.get(c_type, 0),
                'bought': False,
                'buyer': None
            })
        
        return containers
    
    def get_available_containers(self):
        return [c for c in self.containers if not c['bought']]
    
    def buy_container(self, player_id: str, container_id: int):
        player = self.players[player_id]
        container = next((c for c in self.containers if c['id'] == container_id and not c['bought']), None)
        
        if not container:
            self.message = "❌ Контейнер уже куплен!"
            return False
        
        if player['chips'] < container['price']:
            self.message = f"❌ Недостаточно фишек! Нужно {container['price']}, есть {player['chips']}"
            return False
        
        player['chips'] -= container['price']
        player['containers'].append(container['type'])
        container['bought'] = True
        container['buyer'] = player_id
        player['score'] = sum(VALUES.get(c, 0) for c in player['containers'])
        
        self.message = f"✅ {player['name']} купил контейнер! Внутри: {container['type']} (+{container['value']} очков)"
        return True
    
    def use_xray(self, player_id: str, container_id: int):
        player = self.players[player_id]
        
        if player['used_xray']:
            self.message = "❌ Рентген уже использован!"
            return None
        
        container = next((c for c in self.containers if c['id'] == container_id and not c['bought']), None)
        
        if not container:
            self.message = "❌ Контейнер не найден или уже куплен!"
            return None
        
        player['used_xray'] = True
        self.message = f"🔍 {player['name']} использовал РЕНТГЕН!"
        return container['type']
    
    def use_intercept(self, player_id: str):
        player = self.players[player_id]
        other_id = 'player2' if player_id == 'player1' else 'player1'
        other = self.players[other_id]
        
        if player['used_intercept']:
            self.message = "❌ Перехват уже использован!"
            return False
        
        last_bought = None
        for c in reversed(self.containers):
            if c['bought'] and c['buyer'] == other_id:
                last_bought = c
                break
        
        if not last_bought:
            self.message = "❌ Нет контейнеров для перехвата!"
            return False
        
        other['containers'].remove(last_bought['type'])
        other['chips'] += last_bought['price']
        other['score'] = sum(VALUES.get(c, 0) for c in other['containers'])
        
        player['containers'].append(last_bought['type'])
        player['score'] = sum(VALUES.get(c, 0) for c in player['containers'])
        last_bought['buyer'] = player_id
        player['used_intercept'] = True
        
        self.message = f"🦅 {player['name']} ПЕРЕХВАТИЛ контейнер с {last_bought['type']}!"
        return True
    
    def start_auction(self, container_id: int):
        container = next((c for c in self.containers if c['id'] == container_id), None)
        if not container:
            return False
        
        self.auction_active = True
        self.auction_data = {
            'container_id': container_id,
            'current_price': container['price'],
            'raise_count': 0,
            'current_bidder': None,
            'passed': []
        }
        self.message = f"🔥 Начался аукцион! Старт: {container['price']} фишек"
        return True
    
    def auction_bid(self, player_id: str, action: str):
        if not self.auction_active:
            self.message = "❌ Аукцион не активен!"
            return False
        
        player = self.players[player_id]
        auction = self.auction_data
        
        if player_id in auction['passed']:
            self.message = "❌ Вы уже пасовали!"
            return False
        
        container = next((c for c in self.containers if c['id'] == auction['container_id']), None)
        if not container or container['bought']:
            self.message = "❌ Контейнер уже куплен!"
            return False
        
        if action == 'raise':
            if auction['raise_count'] >= AUCTION_MAX_RAISES:
                self.message = "❌ Достигнут лимит повышений!"
                return False
            
            if player['chips'] < auction['current_price'] + AUCTION_STEP:
                self.message = f"❌ Недостаточно фишек для повышения!"
                return False
            
            auction['current_price'] += AUCTION_STEP
            auction['raise_count'] += 1
            auction['current_bidder'] = player_id
            
            self.message = f"⬆️ {player['name']} повысил до {auction['current_price']} фишек"
            
            if auction['raise_count'] >= AUCTION_MAX_RAISES:
                if self.buy_container(player_id, container['id']):
                    self.auction_active = False
                    self.message = f"🏆 {player['name']} выиграл аукцион за {auction['current_price']} фишек!"
            
            return True
            
        elif action == 'pass':
            auction['passed'].append(player_id)
            self.message = f"🙅 {player['name']} пасует"
            
            if len(auction['passed']) >= 2:
                self.auction_active = False
                if auction['current_bidder']:
                    winner_id = auction['current_bidder']
                    if self.buy_container(winner_id, container['id']):
                        self.message = f"🏆 {self.players[winner_id]['name']} выиграл аукцион за {auction['current_price']} фишек!"
                else:
                    self.message = "❌ Аукцион завершен без победителя!"
            
            return True
            
        elif action == 'buy':
            if player['chips'] < auction['current_price']:
                self.message = f"❌ Недостаточно фишек! Нужно {auction['current_price']}"
                return False
            
            if self.buy_container(player_id, container['id']):
                self.auction_active = False
                self.message = f"✅ {player['name']} купил контейнер за {auction['current_price']} фишек!"
                return True
        
        return False
    
    def check_round_end(self):
        available = self.get_available_containers()
        if not available:
            return True
        
        if time.time() - self.round_start_time > ROUND_TIMEOUT:
            for c in available:
                c['bought'] = True
            self.message = "⏰ Время вышло! Не купленные контейнеры ушли в сброс"
            return True
        
        return False
    
    def check_game_over(self):
        if self.current_round >= MAX_ROUNDS:
            p1 = self.players['player1']
            p2 = self.players['player2']
            
            if len(p1['containers']) < MIN_CONTAINERS:
                self.winner = 'player2'
                self.game_over = True
                self.message = f"🏆 {p2['name']} победил! У {p1['name']} меньше 2 контейнеров!"
            elif len(p2['containers']) < MIN_CONTAINERS:
                self.winner = 'player1'
                self.game_over = True
                self.message = f"🏆 {p1['name']} победил! У {p2['name']} меньше 2 контейнеров!"
            elif p1['score'] > p2['score']:
                self.winner = 'player1'
                self.game_over = True
                self.message = f"🏆 {p1['name']} победил со счетом {p1['score']} против {p2['score']}!"
            elif p2['score'] > p1['score']:
                self.winner = 'player2'
                self.game_over = True
                self.message = f"🏆 {p2['name']} победил со счетом {p2['score']} против {p1['score']}!"
            else:
                if p1['chips'] > p2['chips']:
                    self.winner = 'player1'
                elif p2['chips'] > p1['chips']:
                    self.winner = 'player2'
                else:
                    self.winner = 'draw'
                self.game_over = True
                self.message = f"🤝 Ничья! {'Победа по фишкам!' if self.winner != 'draw' else 'Абсолютная ничья!'}"
            
            return True
        
        return False
    
    def next_round(self):
        self.current_round += 1
        self.containers = self.generate_containers()
        self.round_start_time = time.time()
        self.auction_active = False
        self.message = f"🔄 Раунд {self.current_round} начался!"

# ---------------------------
# 3. SOCKET.IO ОБРАБОТЧИКИ
# ---------------------------

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")
    if request.sid in game_players:
        game_id = game_players[request.sid]
        if game_id in games:
            game = games[game_id]
            for player_id in ['player1', 'player2']:
                if game.players[player_id]['sid'] == request.sid:
                    game.players[player_id]['sid'] = None
                    game.players_connected[player_id] = False
                    emit('player_disconnected', {'player_id': player_id}, room=game_id)

@socketio.on('join_game')
def handle_join_game(data):
    game_id = data.get('game_id', 'default')
    player_id = data.get('player_id')
    
    join_room(game_id)
    game_players[request.sid] = game_id
    
    if game_id not in games:
        games[game_id] = ContainerGame()
    
    game = games[game_id]
    
    if player_id == 'player1' and not game.players_connected['player1']:
        game.players['player1']['sid'] = request.sid
        game.players_connected['player1'] = True
    elif player_id == 'player2' and not game.players_connected['player2']:
        game.players['player2']['sid'] = request.sid
        game.players_connected['player2'] = True
    else:
        if not game.players_connected['player1']:
            game.players['player1']['sid'] = request.sid
            game.players_connected['player1'] = True
            player_id = 'player1'
        elif not game.players_connected['player2']:
            game.players['player2']['sid'] = request.sid
            game.players_connected['player2'] = True
            player_id = 'player2'
        else:
            emit('error', {'message': 'Игра полна!'})
            return
    
    emit('player_assigned', {
        'player_id': player_id,
        'name': game.players[player_id]['name']
    })
    
    send_game_state(game_id)
    
    if game.players_connected['player1'] and game.players_connected['player2']:
        emit('game_ready', {'message': 'Оба игрока подключены! Начинаем!'}, room=game_id)

@socketio.on('start_game')
def handle_start_game(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    if game.players_connected['player1'] and game.players_connected['player2']:
        game.started = True
        game.next_round()
        send_game_state(game_id)
    else:
        emit('error', {'message': 'Оба игрока должны быть подключены!'})

@socketio.on('buy_container')
def handle_buy_container(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    player_id = data.get('player_id')
    container_id = data.get('container_id')
    
    if not game.started or game.game_over:
        return
    
    if game.auction_active:
        emit('error', {'message': 'Идет аукцион!'}, room=game_id)
        return
    
    game.buy_container(player_id, container_id)
    send_game_state(game_id)

@socketio.on('use_xray')
def handle_use_xray(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    player_id = data.get('player_id')
    container_id = data.get('container_id')
    
    if not game.started or game.game_over:
        return
    
    result = game.use_xray(player_id, container_id)
    
    if result:
        emit('xray_result', {
            'container_id': container_id,
            'type': result,
            'value': VALUES.get(result, 0)
        }, room=request.sid)
    
    send_game_state(game_id)

@socketio.on('use_intercept')
def handle_use_intercept(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    player_id = data.get('player_id')
    
    if not game.started or game.game_over:
        return
    
    game.use_intercept(player_id)
    send_game_state(game_id)

@socketio.on('auction_bid')
def handle_auction_bid(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    player_id = data.get('player_id')
    action = data.get('action')
    
    if not game.started or game.game_over:
        return
    
    game.auction_bid(player_id, action)
    send_game_state(game_id)

@socketio.on('next_round')
def handle_next_round(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    
    if game.check_round_end() and not game.game_over:
        game.next_round()
        send_game_state(game_id)

def send_game_state(game_id):
    if game_id not in games:
        return
    
    game = games[game_id]
    
    if game.check_game_over():
        pass
    
    state = {
        'current_round': game.current_round,
        'max_rounds': MAX_ROUNDS,
        'game_over': game.game_over,
        'winner': game.winner,
        'message': game.message,
        'auction_active': game.auction_active,
        'started': game.started,
        'players': {}
    }
    
    for player_id, player_data in game.players.items():
        state['players'][player_id] = {
            'name': player_data['name'],
            'chips': player_data['chips'],
            'score': player_data['score'],
            'containers': player_data['containers'],
            'containers_count': len(player_data['containers']),
            'used_xray': player_data['used_xray'],
            'used_intercept': player_data['used_intercept'],
            'connected': game.players_connected[player_id]
        }
    
    state['containers'] = []
    for c in game.containers:
        state['containers'].append({
            'id': c['id'],
            'price': c['price'],
            'bought': c['bought'],
            'buyer': c['buyer'],
            'type': c['type'] if c['bought'] else None,
            'value': c['value'] if c['bought'] else None
        })
    
    if game.auction_active:
        state['auction'] = {
            'container_id': game.auction_data['container_id'],
            'current_price': game.auction_data['current_price'],
            'raise_count': game.auction_data['raise_count'],
            'max_raises': AUCTION_MAX_RAISES,
            'current_bidder': game.auction_data['current_bidder'],
            'passed': game.auction_data['passed']
        }
    
    if game.started and not game.game_over:
        elapsed = time.time() - game.round_start_time
        state['time_remaining'] = max(0, ROUND_TIMEOUT - elapsed)
        
        if game.check_round_end() and not game.game_over:
            state['round_ended'] = True
    
    emit('game_state', state, room=game_id)

# ---------------------------
# 4. ЗАПУСК
# ---------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
