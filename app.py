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

ITEMS = {
    "Алмаз": 500,
    "Золотой слиток": 400,
    "Платина": 350,
    "Серебро": 200,
    "Бронза": 120,
    "Яхта": 200,
    "Дом": 100,
    "Квартира": 85,
    "Машина": 70,
    "Мотоцикл": 55,
    "Мебель": 40,
    "Телевизор": 35,
    "Ноутбук": 30,
    "Телефон": 25,
    "Планшет": 20,
    "Велосипед": -20,
    "Самокат": -15,
    "Ролики": -10,
    "Лыжи": -5,
    "Коньки": -8,
    "Билет в кино": 15,
    "Билет в театр": 25,
    "Билет на концерт": 35,
    "Ресторан": 45,
    "Spa-салон": 55,
    "Путешествие": 80,
    "Круиз": 120,
    "Отель": 65,
    "Вино": 20,
    "Шампанское": 30,
    "Коньяк": 40,
    "Шоколад": 10,
    "Торт": 15,
    "Цветы": 12,
    "Духи": 45,
    "Часы": 60,
    "Кольцо": 90,
    "Ожерелье": 110
}

POOL = []
for item, value in ITEMS.items():
    count = 2 if random.random() > 0.6 else 1
    POOL.extend([item] * count)
random.shuffle(POOL)

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
        self.auction_start_time = None
        self.started = False
        self.players_connected = {'player1': False, 'player2': False}
        self.round_ended = False
        self.waiting_for_next_round = False
        self.final_results_shown = False
        
    def shuffle_pool(self):
        pool = []
        for item, value in ITEMS.items():
            count = 2 if random.random() > 0.6 else 1
            pool.extend([item] * count)
        random.shuffle(pool)
        return pool
    
    def generate_containers(self):
        if not self.pool:
            self.pool = self.shuffle_pool()
        
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
    
    def get_available_containers(self):
        return [c for c in self.containers if not c['bought']]
    
    def buy_container(self, player_id: str, container_id: int):
        player = self.players[player_id]
        container = next((c for c in self.containers if c['id'] == container_id and not c['bought']), None)
        
        if not container:
            self.message = "Контейнер уже куплен"
            return False
        
        if player['chips'] < container['price']:
            self.message = f"Недостаточно фишек! Нужно {container['price']}, есть {player['chips']}"
            return False
        
        # ✅ СПИСЫВАЕМ ФИШКИ
        player['chips'] -= container['price']
        player['containers'].append(container['type'])
        container['bought'] = True
        container['buyer'] = player_id
        
        self.message = f"{player['name']} купил контейнер"
        return True
    
    def use_xray(self, player_id: str, container_id: int):
        player = self.players[player_id]
        
        if player['used_xray']:
            self.message = "Рентген уже использован"
            return None
        
        container = next((c for c in self.containers if c['id'] == container_id and not c['bought']), None)
        
        if not container:
            self.message = "Контейнер не найден или уже куплен"
            return None
        
        player['used_xray'] = True
        self.message = f"{player['name']} использовал рентген"
        return container['type']
    
    def use_intercept(self, player_id: str):
        player = self.players[player_id]
        other_id = 'player2' if player_id == 'player1' else 'player1'
        other = self.players[other_id]
        
        if player['used_intercept']:
            self.message = "Перехват уже использован"
            return False
        
        # Ищем последний купленный контейнер другим игроком
        last_bought = None
        for c in reversed(self.containers):
            if c['bought'] and c['buyer'] == other_id:
                last_bought = c
                break
        
        if not last_bought:
            self.message = "Нет контейнеров для перехвата"
            return False
        
        # ✅ ПЕРЕХВАТ: забираем контейнер, фишки возвращаем
        # 1. Удаляем контейнер у другого игрока
        other['containers'].remove(last_bought['type'])
        # 2. Возвращаем фишки другому игроку
        other['chips'] += last_bought['price']
        # 3. Добавляем контейнер перехватившему
        player['containers'].append(last_bought['type'])
        # 4. Меняем владельца
        last_bought['buyer'] = player_id
        player['used_intercept'] = True
        
        self.message = f"{player['name']} перехватил контейнер"
        return True
    
    def start_auction(self, container_id: int):
        container = next((c for c in self.containers if c['id'] == container_id), None)
        if not container:
            return False
        
        self.auction_active = True
        self.auction_start_time = time.time()
        self.auction_data = {
            'container_id': container_id,
            'current_price': container['price'],
            'raise_count': 0,
            'current_bidder': None,
            'passed': [],
            'players_in': ['player1', 'player2']
        }
        self.message = f"Начался аукцион! Старт: {container['price']} фишек (40 секунд)"
        return True
    
    def auction_bid(self, player_id: str, action: str):
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
        
        # Проверяем таймаут аукциона (40 секунд)
        if time.time() - self.auction_start_time > AUCTION_TIMEOUT:
            self.auction_active = False
            if auction['current_bidder']:
                winner_id = auction['current_bidder']
                if self.buy_container(winner_id, container['id']):
                    self.message = f"Время вышло! {self.players[winner_id]['name']} забирает контейнер за {auction['current_price']} фишек"
                    self.check_round_end()
            else:
                container['bought'] = True
                self.message = "Время аукциона вышло! Контейнер ушел в сброс"
                self.check_round_end()
            return True
        
        if action == 'raise':
            if auction['raise_count'] >= AUCTION_MAX_RAISES:
                self.message = "Достигнут лимит повышений (3)"
                return False
            
            if player['chips'] < auction['current_price'] + AUCTION_STEP:
                self.message = f"Недостаточно фишек для повышения"
                return False
            
            auction['current_price'] += AUCTION_STEP
            auction['raise_count'] += 1
            auction['current_bidder'] = player_id
            
            self.message = f"{player['name']} повысил до {auction['current_price']} фишек"
            
            if auction['raise_count'] >= AUCTION_MAX_RAISES:
                if self.buy_container(player_id, container['id']):
                    self.auction_active = False
                    self.message = f"{player['name']} выиграл аукцион за {auction['current_price']} фишек"
                    self.check_round_end()
            
            return True
            
        elif action == 'pass':
            auction['passed'].append(player_id)
            self.message = f"{player['name']} пасует"
            
            if len(auction['passed']) >= 2:
                self.auction_active = False
                if auction['current_bidder']:
                    winner_id = auction['current_bidder']
                    if self.buy_container(winner_id, container['id']):
                        self.message = f"{self.players[winner_id]['name']} выиграл аукцион за {auction['current_price']} фишек"
                        self.check_round_end()
                else:
                    container['bought'] = True
                    self.message = "Аукцион завершен без победителя"
                    self.check_round_end()
            
            return True
            
        elif action == 'buy':
            if player['chips'] < auction['current_price']:
                self.message = f"Недостаточно фишек! Нужно {auction['current_price']}"
                return False
            
            if self.buy_container(player_id, container['id']):
                self.auction_active = False
                self.message = f"{player['name']} купил контейнер за {auction['current_price']} фишек"
                self.check_round_end()
                return True
        
        return False
    
    def check_round_end(self):
        available = self.get_available_containers()
        
        if not available:
            self.round_ended = True
            self.waiting_for_next_round = True
            if not self.game_over:
                self.message = "Все контейнеры куплены! Нажмите 'Следующий раунд'"
            return True
        
        if time.time() - self.round_start_time > ROUND_TIMEOUT:
            for c in available:
                c['bought'] = True
            self.round_ended = True
            self.waiting_for_next_round = True
            if not self.game_over:
                self.message = "Время вышло! Не купленные контейнеры ушли в сброс"
            return True
        
        return False
    
    def calculate_final_scores(self):
        for player_id in self.players:
            player = self.players[player_id]
            player['score'] = sum(VALUES.get(c, 0) for c in player['containers'])
    
    def check_game_over(self):
        if self.current_round >= MAX_ROUNDS and self.waiting_for_next_round:
            self.calculate_final_scores()
            
            p1 = self.players['player1']
            p2 = self.players['player2']
            
            if len(p1['containers']) < MIN_CONTAINERS:
                self.winner = 'player2'
                self.game_over = True
                self.message = f"{p2['name']} победил! У {p1['name']} меньше 2 контейнеров"
            elif len(p2['containers']) < MIN_CONTAINERS:
                self.winner = 'player1'
                self.game_over = True
                self.message = f"{p1['name']} победил! У {p2['name']} меньше 2 контейнеров"
            elif p1['score'] > p2['score']:
                self.winner = 'player1'
                self.game_over = True
                self.message = f"{p1['name']} победил со счетом {p1['score']} против {p2['score']}"
            elif p2['score'] > p1['score']:
                self.winner = 'player2'
                self.game_over = True
                self.message = f"{p2['name']} победил со счетом {p2['score']} против {p1['score']}"
            else:
                if p1['chips'] > p2['chips']:
                    self.winner = 'player1'
                elif p2['chips'] > p1['chips']:
                    self.winner = 'player2'
                else:
                    self.winner = 'draw'
                self.game_over = True
                self.message = f"Ничья! {'Победа по фишкам' if self.winner != 'draw' else 'Абсолютная ничья'}"
            
            self.final_results_shown = True
            return True
        
        return False
    
    def next_round(self):
        if self.current_round >= MAX_ROUNDS:
            return
        
        self.current_round += 1
        self.containers = self.generate_containers()
        self.round_start_time = time.time()
        self.auction_active = False
        self.round_ended = False
        self.waiting_for_next_round = False
        self.message = f"Раунд {self.current_round} начался"

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
    
    if player_id == 'player1' and game.players_connected['player1']:
        emit('error', {'message': 'Игрок 1 уже подключен'})
        return
    elif player_id == 'player2' and game.players_connected['player2']:
        emit('error', {'message': 'Игрок 2 уже подключен'})
        return
    
    if player_id == 'player1':
        game.players['player1']['sid'] = request.sid
        game.players_connected['player1'] = True
    elif player_id == 'player2':
        game.players['player2']['sid'] = request.sid
        game.players_connected['player2'] = True
    else:
        emit('error', {'message': 'Неверный ID игрока'})
        return
    
    emit('player_assigned', {
        'player_id': player_id,
        'name': game.players[player_id]['name']
    })
    
    send_game_state(game_id)
    
    if game.players_connected['player1'] and game.players_connected['player2']:
        emit('game_ready', {'message': 'Оба игрока подключены! Начинаем'}, room=game_id)

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
        emit('error', {'message': 'Оба игрока должны быть подключены'})

@socketio.on('buy_container')
def handle_buy_container(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    player_id = data.get('player_id')
    container_id = data.get('container_id')
    
    if not game.started:
        emit('error', {'message': 'Игра еще не началась'})
        return
    
    if game.game_over:
        emit('error', {'message': 'Игра уже закончена'})
        return
    
    if game.auction_active:
        emit('error', {'message': 'Идет аукцион! Нельзя купить напрямую'}, room=game_id)
        return
    
    container = next((c for c in game.containers if c['id'] == container_id and not c['bought']), None)
    if container:
        if game.buy_container(player_id, container_id):
            emit('purchase_result', {
                'container_id': container_id,
                'type': container['type'],
                'value': container['value']
            }, room=request.sid)
    
    game.check_round_end()
    send_game_state(game_id)

@socketio.on('use_xray')
def handle_use_xray(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    player_id = data.get('player_id')
    container_id = data.get('container_id')
    
    if not game.started:
        emit('error', {'message': 'Игра еще не началась'})
        return
    
    if game.game_over:
        emit('error', {'message': 'Игра уже закончена'})
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
    
    if not game.started:
        emit('error', {'message': 'Игра еще не началась'})
        return
    
    if game.game_over:
        emit('error', {'message': 'Игра уже закончена'})
        return
    
    # Сохраняем информацию до перехвата
    other_id = 'player2' if player_id == 'player1' else 'player1'
    last_bought = None
    for c in reversed(game.containers):
        if c['bought'] and c['buyer'] == other_id:
            last_bought = c
            break
    
    if last_bought:
        if game.use_intercept(player_id):
            emit('intercept_result', {
                'container_id': last_bought['id'],
                'type': last_bought['type'],
                'value': last_bought['value']
            }, room=request.sid)
    
    send_game_state(game_id)

@socketio.on('auction_bid')
def handle_auction_bid(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    player_id = data.get('player_id')
    action = data.get('action')
    
    if not game.started:
        emit('error', {'message': 'Игра еще не началась'})
        return
    
    if game.game_over:
        emit('error', {'message': 'Игра уже закончена'})
        return
    
    if not game.auction_active:
        emit('error', {'message': 'Аукцион не активен'})
        return
    
    container_id = game.auction_data.get('container_id') if game.auction_active else None
    
    game.auction_bid(player_id, action)
    
    if not game.auction_active and container_id:
        container = next((c for c in game.containers if c['id'] == container_id), None)
        if container and container['bought']:
            emit('purchase_result', {
                'container_id': container_id,
                'type': container['type'],
                'value': container['value']
            }, room=game.players[container['buyer']]['sid'])
    
    send_game_state(game_id)

@socketio.on('next_round')
def handle_next_round(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    game = games[game_id]
    
    if game.game_over:
        emit('error', {'message': 'Игра уже закончена'})
        return
    
    if game.waiting_for_next_round:
        game.next_round()
        send_game_state(game_id)
    else:
        emit('error', {'message': 'Раунд еще не закончен'})

@socketio.on('reset_game')
def handle_reset_game(data):
    game_id = game_players.get(request.sid)
    if not game_id or game_id not in games:
        return
    
    games[game_id] = ContainerGame()
    game = games[game_id]
    for player_id in ['player1', 'player2']:
        if game.players[player_id]['sid']:
            game.players_connected[player_id] = True
    
    send_game_state(game_id)

def send_game_state(game_id):
    if game_id not in games:
        return
    
    game = games[game_id]
    
    if game.current_round >= MAX_ROUNDS and game.waiting_for_next_round:
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
        'waiting_for_next_round': game.waiting_for_next_round,
        'final_results_shown': game.final_results_shown,
        'players': {},
        'containers': []
    }
    
    # Отправляем данные игроков
    for player_id, player_data in game.players.items():
        state['players'][player_id] = {
            'name': player_data['name'],
            'chips': player_data['chips'],
            'containers': player_data['containers'],
            'containers_count': len(player_data['containers']),
            'used_xray': player_data['used_xray'],
            'used_intercept': player_data['used_intercept'],
            'connected': game.players_connected[player_id]
        }
        if game.game_over:
            state['players'][player_id]['score'] = player_data['score']
        else:
            state['players'][player_id]['score'] = None
    
    # Отправляем доступные контейнеры
    for c in game.containers:
        if not c['bought']:
            state['containers'].append({
                'id': c['id'],
                'price': c['price'],
                'bought': False
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
        game.check_round_end()
    
    emit('game_state', state, room=game_id)

# ---------------------------
# 4. ЗАПУСК
# ---------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
