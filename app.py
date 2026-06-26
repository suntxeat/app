from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
import random
import time
import os
import threading

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
# 2. ХРАНИЛИЩЕ ИГР
# ---------------------------

games = {}
game_players = {}

class Game:
    def __init__(self):
        self.reset()
    
    def reset(self):
        # Создаем пул предметов
        self.pool = []
        for item, val in ITEMS.items():
            count = 2 if random.random() > 0.6 else 1
            self.pool.extend([item] * count)
        random.shuffle(self.pool)
        
        self.round = 0
        self.players = {
            'p1': {'name': 'Игрок 1', 'chips': 150, 'containers': [], 'score': 0, 
                   'xray': False, 'intercept': False, 'sid': None},
            'p2': {'name': 'Игрок 2', 'chips': 150, 'containers': [], 'score': 0,
                   'xray': False, 'intercept': False, 'sid': None}
        }
        self.containers = []
        self.auction = None
        self.game_over = False
        self.winner = None
        self.message = "Добро пожаловать!"
        self.started = False
        self.connected = {'p1': False, 'p2': False}
        self.round_done = False
        self.waiting = False
        self.round_start = 0
        self.auction_start = 0
        self.timer_running = False
        
    def create_containers(self):
        """Создает контейнеры для раунда (1-5 штук)"""
        if not self.pool:
            self.pool = []
            for item, val in ITEMS.items():
                count = 2 if random.random() > 0.6 else 1
                self.pool.extend([item] * count)
            random.shuffle(self.pool)
        
        count = random.randint(1, 5)
        count = min(count, len(self.pool))
        containers = []
        
        for i in range(count):
            if not self.pool:
                break
            item = self.pool.pop()
            price = random.randint(5, 80)
            containers.append({
                'id': i,
                'item': item,
                'price': price,
                'value': VALUES.get(item, 0),
                'bought': False,
                'buyer': None
            })
        return containers
    
    def get_available(self):
        return [c for c in self.containers if not c['bought']]
    
    def buy_container(self, pid, cid):
        player = self.players[pid]
        container = next((c for c in self.containers if c['id'] == cid and not c['bought']), None)
        
        if not container:
            self.message = "Контейнер уже куплен"
            return False
        
        if player['chips'] < container['price']:
            self.message = f"Не хватает фишек! Нужно {container['price']}"
            return False
        
        player['chips'] -= container['price']
        player['containers'].append(container['item'])
        container['bought'] = True
        container['buyer'] = pid
        
        self.message = f"{player['name']} купил контейнер"
        return True
    
    def use_xray(self, pid, cid):
        player = self.players[pid]
        
        if player['xray']:
            self.message = "Рентген уже использован"
            return None
        
        container = next((c for c in self.containers if c['id'] == cid and not c['bought']), None)
        if not container:
            self.message = "Контейнер не найден"
            return None
        
        player['xray'] = True
        self.message = f"{player['name']} использовал рентген"
        return container['item']
    
    def use_intercept(self, pid):
        player = self.players[pid]
        other = 'p2' if pid == 'p1' else 'p1'
        other_player = self.players[other]
        
        if player['intercept']:
            self.message = "Перехват уже использован"
            return False
        
        # Находим последний купленный контейнер соперника
        last = None
        for c in reversed(self.containers):
            if c['bought'] and c['buyer'] == other:
                last = c
                break
        
        if not last:
            self.message = "Нет контейнеров для перехвата"
            return False
        
        # Перехват
        other_player['containers'].remove(last['item'])
        other_player['chips'] += last['price']
        player['containers'].append(last['item'])
        last['buyer'] = pid
        player['intercept'] = True
        
        self.message = f"{player['name']} перехватил контейнер"
        return True
    
    def start_auction(self, cid):
        """Запускает аукцион"""
        container = next((c for c in self.containers if c['id'] == cid and not c['bought']), None)
        if not container:
            return False
        
        self.auction = {
            'id': cid,
            'price': container['price'],
            'raises': 0,
            'leader': None,
            'passed': [],
            'start': time.time()
        }
        self.auction_start = time.time()
        self.message = f"Аукцион! Старт: {container['price']} фишек"
        return True
    
    def auction_bid(self, pid, action):
        if not self.auction:
            self.message = "Аукцион не активен"
            return False
        
        player = self.players[pid]
        auction = self.auction
        
        if pid in auction['passed']:
            self.message = "Вы уже пасовали"
            return False
        
        container = next((c for c in self.containers if c['id'] == auction['id']), None)
        if not container or container['bought']:
            self.message = "Контейнер уже куплен"
            return False
        
        # Проверка таймаута аукциона
        if time.time() - self.auction_start > AUCTION_TIMEOUT:
            self.auction = None
            if auction['leader']:
                if self.buy_container(auction['leader'], container['id']):
                    self.message = f"Время вышло! {self.players[auction['leader']]['name']} забирает"
            else:
                container['bought'] = True
                self.message = "Время аукциона вышло!"
            self.check_round()
            return True
        
        if action == 'raise':
            if auction['raises'] >= AUCTION_MAX_RAISES:
                self.message = "Достигнут лимит повышений (3)"
                return False
            
            if player['chips'] < auction['price'] + AUCTION_STEP:
                self.message = "Не хватает фишек для повышения"
                return False
            
            auction['price'] += AUCTION_STEP
            auction['raises'] += 1
            auction['leader'] = pid
            
            self.message = f"{player['name']} повысил до {auction['price']}"
            
            if auction['raises'] >= AUCTION_MAX_RAISES:
                if self.buy_container(pid, container['id']):
                    self.auction = None
                    self.message = f"{player['name']} выиграл аукцион!"
                    self.check_round()
            return True
            
        elif action == 'pass':
            auction['passed'].append(pid)
            self.message = f"{player['name']} пасует"
            
            if len(auction['passed']) >= 2:
                self.auction = None
                container['bought'] = True
                self.message = "Оба пасовали! Контейнер ушел в сброс"
                self.check_round()
            elif len(auction['passed']) == 1:
                # Один пасовал - второй получает контейнер
                winner = 'p1' if 'p1' not in auction['passed'] else 'p2'
                if self.buy_container(winner, container['id']):
                    self.auction = None
                    self.message = f"{self.players[winner]['name']} получает контейнер (соперник пасовал)"
                    self.check_round()
            return True
            
        elif action == 'buy':
            if player['chips'] < auction['price']:
                self.message = "Не хватает фишек"
                return False
            
            if self.buy_container(pid, container['id']):
                self.auction = None
                self.message = f"{player['name']} купил контейнер!"
                self.check_round()
                return True
        
        return False
    
    def check_round(self):
        available = self.get_available()
        
        if not available:
            self.round_done = True
            self.waiting = True
            self.timer_running = False
            if not self.game_over:
                self.message = "Все контейнеры куплены!"
            return True
        
        if self.started and time.time() - self.round_start > ROUND_TIMEOUT:
            for c in available:
                c['bought'] = True
            self.round_done = True
            self.waiting = True
            self.timer_running = False
            if not self.game_over:
                self.message = "Время раунда вышло!"
            return True
        
        return False
    
    def check_game(self):
        if self.round >= MAX_ROUNDS and self.waiting:
            # Подсчет очков
            for p in self.players.values():
                p['score'] = sum(VALUES.get(c, 0) for c in p['containers'])
            
            p1, p2 = self.players['p1'], self.players['p2']
            
            if len(p1['containers']) < MIN_CONTAINERS:
                self.winner = 'p2'
            elif len(p2['containers']) < MIN_CONTAINERS:
                self.winner = 'p1'
            elif p1['score'] > p2['score']:
                self.winner = 'p1'
            elif p2['score'] > p1['score']:
                self.winner = 'p2'
            else:
                if p1['chips'] > p2['chips']:
                    self.winner = 'p1'
                elif p2['chips'] > p1['chips']:
                    self.winner = 'p2'
                else:
                    self.winner = 'draw'
            
            self.game_over = True
            self.timer_running = False
            return True
        return False
    
    def next_round(self):
        if self.round >= MAX_ROUNDS:
            return
        
        self.round += 1
        self.containers = self.create_containers()
        self.round_start = time.time()
        self.timer_running = True
        self.auction = None
        self.round_done = False
        self.waiting = False
        self.message = f"Раунд {self.round} начался!"
        
        # Если только 1 контейнер - запускаем аукцион
        available = self.get_available()
        if len(available) == 1:
            self.start_auction(available[0]['id'])

# ---------------------------
# 3. ТАЙМЕР
# ---------------------------

def start_timer(gid):
    def timer_loop():
        while gid in games:
            game = games.get(gid)
            if not game:
                break
            
            if not game.started or game.game_over:
                time.sleep(1)
                continue
            
            # Проверяем раунд
            if not game.round_done:
                game.check_round()
            
            # Проверяем аукцион
            if game.auction:
                if time.time() - game.auction_start > AUCTION_TIMEOUT:
                    cid = game.auction['id']
                    container = next((c for c in game.containers if c['id'] == cid), None)
                    if container and not container['bought']:
                        if game.auction['leader']:
                            game.buy_container(game.auction['leader'], cid)
                            game.message = f"Время вышло! {game.players[game.auction['leader']]['name']} забирает"
                        else:
                            container['bought'] = True
                            game.message = "Время аукциона вышло!"
                    game.auction = None
                    game.check_round()
            
            send_state(gid)
            time.sleep(1)
    
    thread = threading.Thread(target=timer_loop, daemon=True)
    thread.start()

# ---------------------------
# 4. ОБРАБОТЧИКИ СОКЕТОВ
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
        gid = game_players[request.sid]
        if gid in games:
            game = games[gid]
            for pid in ['p1', 'p2']:
                if game.players[pid]['sid'] == request.sid:
                    game.players[pid]['sid'] = None
                    game.connected[pid] = False
                    emit('player_disconnected', {'pid': pid}, room=gid)

@socketio.on('join')
def handle_join(data):
    gid = data.get('gid', 'default')
    pid = data.get('pid')
    
    join_room(gid)
    game_players[request.sid] = gid
    
    if gid not in games:
        games[gid] = Game()
    
    game = games[gid]
    
    if pid not in ['p1', 'p2']:
        emit('error', {'msg': 'Неверный ID игрока'})
        return
    
    if game.connected[pid]:
        emit('error', {'msg': f'{pid} уже подключен'})
        return
    
    game.players[pid]['sid'] = request.sid
    game.connected[pid] = True
    
    emit('assigned', {'pid': pid, 'name': game.players[pid]['name']})
    
    send_state(gid)
    
    if game.connected['p1'] and game.connected['p2']:
        emit('ready', {'msg': 'Оба игрока готовы!'}, room=gid)

@socketio.on('start')
def handle_start(data):
    gid = game_players.get(request.sid)
    if not gid or gid not in games:
        return
    
    game = games[gid]
    
    if not (game.connected['p1'] and game.connected['p2']):
        emit('error', {'msg': 'Оба игрока должны быть подключены'})
        return
    
    if not game.started:
        game.started = True
        game.next_round()
        start_timer(gid)
        send_state(gid)

@socketio.on('buy')
def handle_buy(data):
    gid = game_players.get(request.sid)
    if not gid or gid not in games:
        return
    
    game = games[gid]
    pid = data.get('pid')
    cid = data.get('cid')
    
    if not game.started or game.game_over:
        emit('error', {'msg': 'Игра не активна'})
        return
    
    if game.auction:
        emit('error', {'msg': 'Идет аукцион!'})
        return
    
    container = next((c for c in game.containers if c['id'] == cid and not c['bought']), None)
    if container:
        if game.buy_container(pid, cid):
            emit('bought', {
                'item': container['item'],
                'value': container['value']
            }, room=request.sid)
    
    game.check_round()
    send_state(gid)

@socketio.on('xray')
def handle_xray(data):
    gid = game_players.get(request.sid)
    if not gid or gid not in games:
        return
    
    game = games[gid]
    pid = data.get('pid')
    cid = data.get('cid')
    
    if not game.started or game.game_over:
        emit('error', {'msg': 'Игра не активна'})
        return
    
    result = game.use_xray(pid, cid)
    if result:
        emit('xray_result', {
            'item': result,
            'value': VALUES.get(result, 0)
        }, room=request.sid)
    
    send_state(gid)

@socketio.on('intercept')
def handle_intercept(data):
    gid = game_players.get(request.sid)
    if not gid or gid not in games:
        return
    
    game = games[gid]
    pid = data.get('pid')
    
    if not game.started or game.game_over:
        emit('error', {'msg': 'Игра не активна'})
        return
    
    # Сохраняем данные до перехвата
    other = 'p2' if pid == 'p1' else 'p1'
    last = None
    for c in reversed(game.containers):
        if c['bought'] and c['buyer'] == other:
            last = c
            break
    
    if last and game.use_intercept(pid):
        emit('intercept_result', {
            'item': last['item'],
            'value': last['value']
        }, room=request.sid)
    
    send_state(gid)

@socketio.on('auction')
def handle_auction(data):
    gid = game_players.get(request.sid)
    if not gid or gid not in games:
        return
    
    game = games[gid]
    pid = data.get('pid')
    action = data.get('action')
    
    if not game.started or game.game_over:
        emit('error', {'msg': 'Игра не активна'})
        return
    
    if not game.auction:
        emit('error', {'msg': 'Аукцион не активен'})
        return
    
    cid = game.auction['id']
    game.auction_bid(pid, action)
    
    # Если аукцион завершился с покупкой
    if not game.auction:
        container = next((c for c in game.containers if c['id'] == cid), None)
        if container and container['bought']:
            emit('bought', {
                'item': container['item'],
                'value': container['value']
            }, room=game.players[container['buyer']]['sid'])
    
    send_state(gid)

@socketio.on('next_round')
def handle_next_round(data):
    gid = game_players.get(request.sid)
    if not gid or gid not in games:
        return
    
    game = games[gid]
    
    if game.game_over:
        emit('error', {'msg': 'Игра закончена'})
        return
    
    if game.waiting:
        game.next_round()
        send_state(gid)

@socketio.on('reset')
def handle_reset(data):
    gid = game_players.get(request.sid)
    if not gid or gid not in games:
        return
    
    # Полностью сбрасываем игру
    games[gid] = Game()
    game = games[gid]
    
    # Восстанавливаем подключения
    for pid in ['p1', 'p2']:
        if game.players[pid]['sid']:
            game.connected[pid] = True
    
    send_state(gid)

def send_state(gid):
    if gid not in games:
        return
    
    game = games[gid]
    
    if game.round >= MAX_ROUNDS and game.waiting:
        game.check_game()
    
    state = {
        'round': game.round,
        'max': MAX_ROUNDS,
        'over': game.game_over,
        'winner': game.winner,
        'msg': game.message,
        'auction': game.auction is not None,
        'started': game.started,
        'round_done': game.round_done,
        'waiting': game.waiting,
        'players': {},
        'containers': []
    }
    
    for pid, p in game.players.items():
        state['players'][pid] = {
            'name': p['name'],
            'chips': p['chips'],
            'containers': p['containers'],
            'count': len(p['containers']),
            'xray': p['xray'],
            'intercept': p['intercept'],
            'connected': game.connected[pid]
        }
        if game.game_over:
            state['players'][pid]['score'] = p['score']
        else:
            state['players'][pid]['score'] = None
    
    for c in game.containers:
        if not c['bought']:
            state['containers'].append({
                'id': c['id'],
                'price': c['price']
            })
    
    if game.auction:
        state['auction_data'] = {
            'price': game.auction['price'],
            'raises': game.auction['raises'],
            'max': AUCTION_MAX_RAISES,
            'leader': game.auction['leader'],
            'passed': game.auction['passed']
        }
        remaining = max(0, AUCTION_TIMEOUT - (time.time() - game.auction_start))
        state['auction_time'] = remaining
    
    if game.started and not game.game_over and not game.round_done:
        remaining = max(0, ROUND_TIMEOUT - (time.time() - game.round_start))
        state['time'] = remaining
    
    emit('state', state, room=gid)

# ---------------------------
# 5. ЗАПУСК
# ---------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
