from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
import random
import time
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-here')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ---------------------------
# 1. ИГРОВЫЕ КОНСТАНТЫ
# ---------------------------

ITEMS = {
    "Алмаз": 500, "Золотой слиток": 400, "Платина": 350,
    "Серебро": 200, "Бронза": 120, "Яхта": 200,
    "Дом": 100, "Квартира": 85, "Машина": 70,
    "Мотоцикл": 55, "Мебель": 40, "Телевизор": 35,
    "Ноутбук": 30, "Телефон": 25, "Планшет": 20,
    "Велосипед": -20, "Самокат": -15, "Ролики": -10,
    "Лыжи": -5, "Коньки": -8, "Билет в кино": 15,
    "Билет в театр": 25, "Билет на концерт": 35,
    "Ресторан": 45, "Spa-салон": 55, "Путешествие": 80,
    "Круиз": 120, "Отель": 65, "Вино": 20,
    "Шампанское": 30, "Коньяк": 40, "Шоколад": 10,
    "Торт": 15, "Цветы": 12, "Духи": 45,
    "Часы": 60, "Кольцо": 90, "Ожерелье": 110
}

VALUES = ITEMS

MAX_ROUNDS = 3
MIN_CONTAINERS = 2
AUCTION_STEP = 10
AUCTION_MAX_RAISES = 3
ROUND_TIMEOUT = 30
AUCTION_TIMEOUT = 40

# ---------------------------
# 2. УПРАВЛЕНИЕ ИГРАМИ
# ---------------------------

games = {}
game_players = {}

class ContainerGame:
    def __init__(self):
        self.reset()
    
    def reset(self):
        self.pool = self.create_pool()
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
        self.message = "Добро пожаловать!"
        self.round_start_time = 0
        self.auction_start_time = 0
        self.started = False
        self.players_connected = {'player1': False, 'player2': False}
        self.round_ended = False
        self.waiting_for_next = False
        self.timer_running = False
        
    def create_pool(self):
        pool = []
        for item, value in ITEMS.items():
            count = 2 if random.random() > 0.6 else 1
            pool.extend([item] * count)
        random.shuffle(pool)
        return pool
    
    def generate_containers(self):
        if not self.pool:
            self.pool = self.create_pool()
        
        count = random.randint(1, min(5, len(self.pool)))
        containers = []
        
        for _ in range(count):
            if not self.pool:
                break
            c_type = self.pool.pop()
            price = random.randint(5, 80)
            containers.append({
                'id': len(containers),
                'type': c_type,
                'price': price,
                'value': VALUES.get(c_type, 0),
                'bought': False,
                'buyer': None
            })
        return containers
    
    def get_available(self):
        return [c for c in self.containers if not c['bought']]
    
    def buy_container(self, player_id, container_id):
        player = self.players[player_id]
        container = next((c for c in self.containers if c['id'] == container_id and not c['bought']), None)
        
        if not container:
            self.message = "Контейнер уже куплен"
            return False
        
        if player['chips'] < container['price']:
            self.message = f"Недостаточно фишек! Нужно {container['price']}"
            return False
        
        player['chips'] -= container['price']
        player['containers'].append(container['type'])
        container['bought'] = True
        container['buyer'] = player_id
        
        self.message = f"{player['name']} купил контейнер"
        return True
    
    def use_xray(self, player_id, container_id):
        player = self.players[player_id]
        
        if player['used_xray']:
            self.message = "Рентген уже использован"
            return None
        
        container = next((c for c in self.containers if c['id'] == container_id and not c['bought']), None)
        if not container:
            self.message = "Контейнер не найден"
            return None
        
        player['used_xray'] = True
        self.message = f"{player['name']} использовал рентген"
        return container['type']
    
    def use_intercept(self, player_id):
        player = self.players[player_id]
        other_id = 'player2' if player_id == 'player1' else 'player1'
        other = self.players[other_id]
        
        if player['used_intercept']:
            self.message = "Перехват уже использован"
            return False
        
        # Ищем последний купленный контейнер соперника
        last_bought = None
        for c in reversed(self.containers):
            if c['bought'] and c['buyer'] == other_id:
                last_bought = c
                break
        
        if not last_bought:
            self.message = "Нет контейнеров для перехвата"
            return False
        
        # Перехват
        other['containers'].remove(last_bought['type'])
        other['chips'] += last_bought['price']
        player['containers'].append(last_bought['type'])
        last_bought['buyer'] = player_id
        player['used_intercept'] = True
        
        self.message = f"{player['name']} перехватил контейнер"
        return True
    
    def start_auction(self, container_id):
        container = next((c for c in self.containers if c['id'] == container_id), None)
        if not container:
            return False
        
        self.auction_active = True
        self.auction_start_time = time.time()
        self.timer_running = True
        self.auction_data = {
            'container_id': container_id,
            'current_price': container['price'],
            'raise_count': 0,
            'current_bidder': None,
            'passed': []
        }
        self.message = f"Аукцион! Старт: {container['price']} фишек"
        return True
    
    def auction_bid(self, player_id, action):
        if not self.auction_active:
            self.message = "Аукцион не активен"
            return False
        
        player = self.players[player_id]
        auction = self.auction_data
        
        if player_id in auction['passed']:
            self.message = "Вы уже пасовали"
            return False
        
        container = next((c for c in self.containers if c['id'] == auction['container_id']), None)
        if not container or container['bought']:
            self.message = "Контейнер уже куплен"
            return False
        
        # Проверка таймаута аукциона
        if time.time() - self.auction_start_time > AUCTION_TIMEOUT:
            self.auction_active = False
            self.timer_running = False
            if auction['current_bidder']:
                winner = auction['current_bidder']
                if self.buy_container(winner, container['id']):
                    self.message = f"Время вышло! {self.players[winner]['name']} забирает контейнер"
            else:
                container['bought'] = True
                self.message = "Время аукциона вышло!"
            self.check_round_end()
            return True
        
        if action == 'raise':
            if auction['raise_count'] >= AUCTION_MAX_RAISES:
                self.message = "Лимит повышений (3)"
                return False
            
            if player['chips'] < auction['current_price'] + AUCTION_STEP:
                self.message = "Недостаточно фишек"
                return False
            
            auction['current_price'] += AUCTION_STEP
            auction['raise_count'] += 1
            auction['current_bidder'] = player_id
            self.message = f"{player['name']} повысил до {auction['current_price']}"
            
            if auction['raise_count'] >= AUCTION_MAX_RAISES:
                if self.buy_container(player_id, container['id']):
                    self.auction_active = False
                    self.timer_running = False
                    self.message = f"{player['name']} выиграл аукцион!"
                    self.check_round_end()
            return True
            
        elif action == 'pass':
            auction['passed'].append(player_id)
            self.message = f"{player['name']} пасует"
            
            if len(auction['passed']) >= 2:
                self.auction_active = False
                self.timer_running = False
                if auction['current_bidder']:
                    winner = auction['current_bidder']
                    if self.buy_container(winner, container['id']):
                        self.message = f"{self.players[winner]['name']} выиграл аукцион!"
                else:
                    container['bought'] = True
                    self.message = "Аукцион без победителя"
                self.check_round_end()
            return True
            
        elif action == 'buy':
            if player['chips'] < auction['current_price']:
                self.message = "Недостаточно фишек"
                return False
            
            if self.buy_container(player_id, container['id']):
                self.auction_active = False
                self.timer_running = False
                self.message = f"{player['name']} купил контейнер!"
                self.check_round_end()
                return True
        
        return False
    
    def check_round_end(self):
        available = self.get_available()
        
        # Все куплены
        if not available:
            self.round_ended = True
            self.waiting_for_next = True
            self.timer_running = False
            if not self.game_over:
                self.message = "Все контейнеры куплены!"
            return True
        
        # Таймаут раунда
        if self.started and time.time() - self.round_start_time > ROUND_TIMEOUT:
            for c in available:
                c['bought'] = True
            self.round_ended = True
            self.waiting_for_next = True
            self.timer_running = False
            if not self.game_over:
                self.message = "Время вышло!"
            return True
        
        return False
    
    def check_game_over(self):
        if self.current_round >= MAX_ROUNDS and self.waiting_for_next:
            # Подсчет очков
            for p in self.players.values():
                p['score'] = sum(VALUES.get(c, 0) for c in p['containers'])
            
            p1, p2 = self.players['player1'], self.players['player2']
            
            if len(p1['containers']) < MIN_CONTAINERS:
                self.winner = 'player2'
            elif len(p2['containers']) < MIN_CONTAINERS:
                self.winner = 'player1'
            elif p1['score'] > p2['score']:
                self.winner = 'player1'
            elif p2['score'] > p1['score']:
                self.winner = 'player2'
            else:
                self.winner = 'player1' if p1['chips'] > p2['chips'] else ('player2' if p2['chips'] > p1['chips'] else 'draw')
            
            self.game_over = True
            self.timer_running = False
            return True
        return False
    
    def next_round(self):
        if self.current_round >= MAX_ROUNDS:
            return
        
        self.current_round += 1
        self.containers = self.generate_containers()
        self.round_start_time = time.time()
        self.timer_running = True
        self.auction_active = False
        self.round_ended = False
        self.waiting_for_next = False
        self.message = f"Раунд {self.current_round}"

# ---------------------------
# 3. SOCKET.IO
# ---------------------------

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in game_players:
        game_id = game_players[request.sid]
        if game_id in games:
            game = games[game_id]
            for pid in ['player1', 'player2']:
                if game.players[pid]['sid'] == request.sid:
                    game.players[pid]['sid'] = None
                    game.players_connected[pid] = False
                    emit('player_disconnected', {'player_id': pid}, room=game_id)

@socketio.on('join_game')
def handle_join_game(data):
    game_id = data.get('game_id', 'default')
    player_id = data.get('player_id')
    
    join_room(game_id)
    game_players[request.sid] = game_id
    
    if game_id not in games:
        games[game_id] = ContainerGame()
    
    game = games[game_id]
    
    if player_id not in ['player1', 'player2']:
        emit('error', {'message': 'Неверный ID'})
        return
    
    if game.players_connected[player_id]:
        emit('error', {'message': f'{player_id} уже подключен'})
        return
    
    game.players[player_id]['sid'] = request.sid
    game.players_connected[player_id] = True
    
    emit('player_assigned', {
        'player_id': player_id,
        'name': game.players[player_id]['name']
    })
    
    send_state(game_id)
    
    if game.players_connected['player1'] and game.players_connected['player2']:
        emit('game_ready', {'message': 'Оба игрока готовы!'}, room=game_id)

@socketio.on('start_game')
def handle_start_game(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    if not (game.players_connected['player1'] and game.players_connected['player2']):
        emit('error', {'message': 'Оба игрока должны быть подключены'})
        return
    
    if not game.started:
        game.started = True
        game.next_round()
        send_state(game_id)

@socketio.on('buy_container')
def handle_buy_container(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    player_id = data.get('player_id')
    container_id = data.get('container_id')
    
    if not game.started or game.game_over:
        emit('error', {'message': 'Игра не активна'})
        return
    
    if game.auction_active:
        emit('error', {'message': 'Идет аукцион!'})
        return
    
    container = next((c for c in game.containers if c['id'] == container_id and not c['bought']), None)
    if container:
        if game.buy_container(player_id, container_id):
            emit('purchase_result', {
                'type': container['type'],
                'value': container['value']
            }, room=request.sid)
    
    game.check_round_end()
    send_state(game_id)

@socketio.on('use_xray')
def handle_use_xray(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    player_id = data.get('player_id')
    container_id = data.get('container_id')
    
    if not game.started or game.game_over:
        emit('error', {'message': 'Игра не активна'})
        return
    
    result = game.use_xray(player_id, container_id)
    if result:
        emit('xray_result', {
            'type': result,
            'value': VALUES.get(result, 0)
        }, room=request.sid)
    
    send_state(game_id)

@socketio.on('use_intercept')
def handle_use_intercept(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    player_id = data.get('player_id')
    
    if not game.started or game.game_over:
        emit('error', {'message': 'Игра не активна'})
        return
    
    # Сохраняем информацию до перехвата
    other_id = 'player2' if player_id == 'player1' else 'player1'
    last_bought = None
    for c in reversed(game.containers):
        if c['bought'] and c['buyer'] == other_id:
            last_bought = c
            break
    
    if last_bought and game.use_intercept(player_id):
        emit('intercept_result', {
            'type': last_bought['type'],
            'value': last_bought['value']
        }, room=request.sid)
    
    send_state(game_id)

@socketio.on('auction_bid')
def handle_auction_bid(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    player_id = data.get('player_id')
    action = data.get('action')
    
    if not game.started or game.game_over:
        emit('error', {'message': 'Игра не активна'})
        return
    
    if not game.auction_active:
        emit('error', {'message': 'Аукцион не активен'})
        return
    
    container_id = game.auction_data.get('container_id')
    game.auction_bid(player_id, action)
    
    # Если аукцион завершился и кто-то купил
    if not game.auction_active and container_id:
        container = next((c for c in game.containers if c['id'] == container_id), None)
        if container and container['bought']:
            emit('purchase_result', {
                'type': container['type'],
                'value': container['value']
            }, room=game.players[container['buyer']]['sid'])
    
    send_state(game_id)

@socketio.on('next_round')
def handle_next_round(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    
    if game.game_over:
        emit('error', {'message': 'Игра закончена'})
        return
    
    if game.waiting_for_next:
        game.next_round()
        send_state(game_id)

@socketio.on('reset_game')
def handle_reset_game(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    games[game_id] = ContainerGame()
    game = games[game_id]
    for pid in ['player1', 'player2']:
        if game.players[pid]['sid']:
            game.players_connected[pid] = True
    
    send_state(game_id)

def send_state(game_id):
    if game_id not in games:
        return
    
    game = games[game_id]
    
    # Проверяем окончание игры
    if game.current_round >= MAX_ROUNDS and game.waiting_for_next:
        game.check_game_over()
    
    state = {
        'current_round': game.current_round,
        'max_rounds': MAX_ROUNDS,
        'game_over': game.game_over,
        'winner': game.winner,
        'message': game.message,
        'auction_active': game.auction_active,
        'started': game.started,
        'round_ended': game.round_ended,
        'waiting_for_next': game.waiting_for_next,
        'players': {},
        'containers': []
    }
    
    # Игроки
    for pid, pdata in game.players.items():
        state['players'][pid] = {
            'name': pdata['name'],
            'chips': pdata['chips'],
            'containers': pdata['containers'],
            'containers_count': len(pdata['containers']),
            'used_xray': pdata['used_xray'],
            'used_intercept': pdata['used_intercept'],
            'connected': game.players_connected[pid]
        }
        if game.game_over:
            state['players'][pid]['score'] = pdata['score']
        else:
            state['players'][pid]['score'] = None
    
    # Доступные контейнеры
    for c in game.containers:
        if not c['bought']:
            state['containers'].append({
                'id': c['id'],
                'price': c['price']
            })
    
    # Аукцион
    if game.auction_active:
        state['auction'] = {
            'current_price': game.auction_data['current_price'],
            'raise_count': game.auction_data['raise_count'],
            'max_raises': AUCTION_MAX_RAISES,
            'current_bidder': game.auction_data['current_bidder'],
            'passed': game.auction_data['passed']
        }
        # Время аукциона
        elapsed = time.time() - game.auction_start_time
        state['auction_time_remaining'] = max(0, AUCTION_TIMEOUT - elapsed)
    
    # Таймер раунда
    if game.started and not game.game_over and not game.round_ended:
        elapsed = time.time() - game.round_start_time
        state['time_remaining'] = max(0, ROUND_TIMEOUT - elapsed)
        game.check_round_end()
    
    emit('game_state', state, room=game_id)

# ---------------------------
# 4. ЗАПУСК
# ---------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
