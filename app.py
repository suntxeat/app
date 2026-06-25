# app.py - ИСПРАВЛЕННАЯ ВЕРСИЯ

import streamlit as st
import random
import time
from typing import List, Tuple, Optional, Dict
import pandas as pd

# ---------------------------
# 1. ИГРОВЫЕ КОНСТАНТЫ
# ---------------------------

# СЛОВАРЬ ЗНАЧЕНИЙ ДОЛЖЕН БЫТЬ ОПРЕДЕЛЕН ДО ИСПОЛЬЗОВАНИЯ!
VALUES = {"Яхта": 200, "Дом": 100, "Мебель": 40, "Велосипед": -20}

MAX_ROUNDS = 3
MIN_CONTAINERS = 2
AUCTION_STEP = 10
AUCTION_MAX_RAISES = 3
ROUND_TIMEOUT = 30  # секунд

# ---------------------------
# 2. КЛАССЫ
# ---------------------------

class GameState:
    def __init__(self):
        self.reset()
    
    def reset(self):
        self.pool = self.shuffle_pool()
        self.current_round = 0
        self.players = {
            'player1': {'name': 'Игрок 1', 'chips': 150, 'containers': [], 'score': 0, 
                       'used_xray': False, 'used_intercept': False, 'is_ready': False},
            'player2': {'name': 'Игрок 2', 'chips': 150, 'containers': [], 'score': 0,
                       'used_xray': False, 'used_intercept': False, 'is_ready': False}
        }
        self.containers = []
        self.auction_active = False
        self.auction_data = {}
        self.game_over = False
        self.winner = None
        self.turn = 'player1'
        self.message = "Игра началась! Ожидайте..."
        self.round_start_time = time.time()
        self.players_ready = {'player1': False, 'player2': False}
        self.last_action = ""
    
    def shuffle_pool(self):
        pool = ["Яхта"] * 2 + ["Дом"] * 3 + ["Мебель"] * 5 + ["Велосипед"] * 2
        random.shuffle(pool)
        return pool
    
    def generate_containers(self):
        """Генерирует контейнеры со случайными ценами и ресурсами."""
        if not self.pool:
            return []
        
        count = random.randint(1, min(5, len(self.pool)))
        containers = []
        
        for _ in range(count):
            if not self.pool:
                break
            
            # Случайный тип контейнера
            c_type = self.pool.pop()
            # Случайная цена (не зависит от содержимого!)
            price = random.randint(5, 65)
            
            containers.append({
                'id': len(containers),
                'type': c_type,
                'price': price,
                'value': VALUES.get(c_type, 0),  # VALUES уже определена
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
        
        # Покупаем
        player['chips'] -= container['price']
        player['containers'].append(container['type'])
        container['bought'] = True
        container['buyer'] = player_id
        
        # Обновляем очки
        player['score'] = sum(VALUES.get(c, 0) for c in player['containers'])
        
        self.message = f"✅ {player['name']} купил контейнер! Внутри: {container['type']} (+{container['value']} очков)"
        self.last_action = f"buy_{player_id}_{container_id}"
        return True
    
    def use_xray(self, player_id: str, container_id: int):
        player = self.players[player_id]
        
        if player['used_xray']:
            self.message = "❌ Рентген уже использован!"
            return False
        
        container = next((c for c in self.containers if c['id'] == container_id and not c['bought']), None)
        
        if not container:
            self.message = "❌ Контейнер не найден или уже куплен!"
            return False
        
        player['used_xray'] = True
        self.message = f"🔍 {player['name']} использовал РЕНТГЕН! В контейнере: {container['type']} (ценность: {container['value']} очков)"
        self.last_action = f"xray_{player_id}_{container_id}"
        return True
    
    def use_intercept(self, player_id: str):
        player = self.players[player_id]
        other_id = 'player2' if player_id == 'player1' else 'player1'
        other = self.players[other_id]
        
        if player['used_intercept']:
            self.message = "❌ Перехват уже использован!"
            return False
        
        # Находим последний купленный контейнер другим игроком
        last_bought = None
        for c in reversed(self.containers):
            if c['bought'] and c['buyer'] == other_id:
                last_bought = c
                break
        
        if not last_bought:
            self.message = "❌ Нет контейнеров для перехвата!"
            return False
        
        # Перехватываем
        other['containers'].remove(last_bought['type'])
        other['chips'] += last_bought['price']
        other['score'] = sum(VALUES.get(c, 0) for c in other['containers'])
        
        player['containers'].append(last_bought['type'])
        player['score'] = sum(VALUES.get(c, 0) for c in player['containers'])
        last_bought['buyer'] = player_id
        player['used_intercept'] = True
        
        self.message = f"🦅 {player['name']} ПЕРЕХВАТИЛ контейнер с {last_bought['type']}!"
        self.last_action = f"intercept_{player_id}"
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
            'players_in': ['player1', 'player2'],
            'passed': []
        }
        self.message = f"🔥 Начался аукцион за контейнер! Старт: {container['price']} фишек"
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
                # Автоматическая покупка
                if self.buy_container(player_id, container['id']):
                    self.auction_active = False
                    self.message = f"🏆 {player['name']} выиграл аукцион за {auction['current_price']} фишек!"
            
            return True
            
        elif action == 'pass':
            auction['passed'].append(player_id)
            self.message = f"🙅 {player['name']} пасует"
            
            if len(auction['passed']) >= 2:
                # Оба пасовали
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
        # Проверяем, все ли контейнеры куплены
        available = self.get_available_containers()
        if not available:
            return True
        
        # Проверяем таймаут
        if time.time() - self.round_start_time > ROUND_TIMEOUT:
            # Не купленные контейнеры уходят в сброс
            for c in available:
                c['bought'] = True
            self.message = "⏰ Время вышло! Не купленные контейнеры ушли в сброс"
            return True
        
        return False
    
    def check_game_over(self):
        if self.current_round >= MAX_ROUNDS:
            # Подсчет результатов
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
                # Ничья - считаем по фишкам
                if p1['chips'] > p2['chips']:
                    self.winner = 'player1'
                    self.message = f"🤝 Ничья по очкам! {p1['name']} победил по фишкам!"
                elif p2['chips'] > p1['chips']:
                    self.winner = 'player2'
                    self.message = f"🤝 Ничья по очкам! {p2['name']} победил по фишкам!"
                else:
                    self.winner = 'draw'
                    self.message = "🤝 Абсолютная ничья!"
                self.game_over = True
            
            return True
        
        return False
    
    def next_round(self):
        self.current_round += 1
        self.containers = self.generate_containers()
        self.round_start_time = time.time()
        self.auction_active = False
        self.players_ready = {'player1': False, 'player2': False}
        self.message = f"🔄 Раунд {self.current_round} начался!"

# ---------------------------
# 3. СТРАНИЦА ИГРЫ
# ---------------------------

def init_session_state():
    if 'game' not in st.session_state:
        st.session_state.game = GameState()
    if 'player_id' not in st.session_state:
        st.session_state.player_id = None
    if 'game_started' not in st.session_state:
        st.session_state.game_started = False

def get_emoji(container_type: str) -> str:
    emojis = {"Яхта": "⛵", "Дом": "🏠", "Мебель": "🪑", "Велосипед": "🚲"}
    return emojis.get(container_type, "📦")

def main():
    st.set_page_config(
        page_title="Контейнеры - Игра",
        page_icon="🎮",
        layout="wide"
    )
    
    init_session_state()
    game = st.session_state.game
    
    # Стили
    st.markdown("""
        <style>
        .container-card {
            border: 2px solid #ddd;
            border-radius: 10px;
            padding: 15px;
            margin: 10px 0;
            background: #f9f9f9;
        }
        .player-card {
            border: 2px solid #4CAF50;
            border-radius: 10px;
            padding: 15px;
            margin: 10px 0;
            background: #e8f5e9;
        }
        .container-item {
            display: inline-block;
            padding: 10px;
            margin: 5px;
            border: 1px solid #ccc;
            border-radius: 5px;
            background: white;
        }
        .hidden {
            color: #999;
            font-style: italic;
        }
        .auction-box {
            border: 2px solid #ff6b6b;
            border-radius: 10px;
            padding: 15px;
            margin: 10px 0;
            background: #fff3f3;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(255, 107, 107, 0.4); }
            70% { box-shadow: 0 0 0 10px rgba(255, 107, 107, 0); }
            100% { box-shadow: 0 0 0 0 rgba(255, 107, 107, 0); }
        }
        .stButton button {
            width: 100%;
        }
        </style>
    """, unsafe_allow_html=True)
    
    st.title("🎮 Контейнеры - Веб-игра")
    st.caption("Играйте с другом с двух устройств!")
    
    # Выбор игрока
    col1, col2 = st.columns([1, 2])
    
    with col1:
        if not st.session_state.player_id:
            st.subheader("👤 Выберите игрока")
            st.info("Игрок 1 и Игрок 2 должны выбрать свои роли на разных устройствах")
            
            if st.button("🎮 Игрок 1", use_container_width=True):
                st.session_state.player_id = 'player1'
                st.rerun()
            
            if st.button("🎮 Игрок 2", use_container_width=True):
                st.session_state.player_id = 'player2'
                st.rerun()
        else:
            player = game.players[st.session_state.player_id]
            st.success(f"✅ Вы играете как: {player['name']}")
            
            if st.button("🔄 Сменить игрока", use_container_width=True):
                st.session_state.player_id = None
                st.rerun()
    
    with col2:
        if not st.session_state.game_started:
            if st.button("🚀 Начать новую игру", use_container_width=True, type="primary"):
                game.reset()
                game.next_round()
                st.session_state.game_started = True
                st.rerun()
            st.caption("Нажмите после того, как оба игрока выбрали роли")
    
    if not st.session_state.game_started:
        st.info("👆 Нажмите 'Начать новую игру' когда оба игрока готовы!")
        
        # Показываем правила
        with st.expander("📖 Правила игры", expanded=True):
            st.markdown("""
            **Правила игры «Контейнеры»:**
            
            - 🎯 **Цель:** набрать максимальное количество очков из контейнеров
            - 📦 **Минимум:** нужно иметь как минимум 2 контейнера к концу игры
            - 🔄 **Раунды:** игра длится 3 раунда
            - ⏱️ **Таймаут:** 30 секунд на раунд
            - 💰 **Капитал:** у каждого 150 фишек
            
            **Контейнеры:**
            - Генерируются от 1 до 5 контейнеров за раунд
            - Цена случайная (5-65 фишек) - не зависит от содержимого!
            - Содержимое узнается ТОЛЬКО после покупки
            
            **Дополнительные действия (1 раз за игру):**
            - 🔍 **Рентген:** посмотреть содержимое контейнера до покупки
            - 🦅 **Перехват:** забрать последний купленный контейнер у соперника
            
            **Аукцион:**
            - Если выпал 1 контейнер, и оба хотят его купить
            - Можно повышать ставку (шаг 10 фишек)
            - Максимум 3 повышения
            - Победитель забирает контейнер по финальной цене
            """)
        return
    
    if st.session_state.player_id:
        current_player = game.players[st.session_state.player_id]
        other_id = 'player2' if st.session_state.player_id == 'player1' else 'player1'
        other_player = game.players[other_id]
        
        # Основная информация
        col1, col2, col3 = st.columns([1, 1.5, 1])
        
        with col1:
            st.subheader("📊 Статус")
            st.metric("Раунд", f"{game.current_round}/{MAX_ROUNDS}")
            st.metric("📦 Контейнеров на столе", len(game.get_available_containers()))
            if game.message:
                st.info(game.message)
        
        with col2:
            st.subheader(f"👤 {current_player['name']} (ВЫ)")
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.metric("💰 Фишки", current_player['chips'])
            with col_b:
                st.metric("⭐ Очки", current_player['score'])
            with col_c:
                st.metric("📦 Контейнеров", len(current_player['containers']))
            
            if current_player['containers']:
                st.write("**Ваши контейнеры:**")
                for c in current_player['containers']:
                    st.write(f"{get_emoji(c)} {c} (+{VALUES.get(c, 0)})")
            else:
                st.write("*Пока нет контейнеров*")
            
            # Статус способностей
            st.write("**Способности:**")
            st.write(f"🔍 Рентген: {'✅ использован' if current_player['used_xray'] else '❌ доступен'}")
            st.write(f"🦅 Перехват: {'✅ использован' if current_player['used_intercept'] else '❌ доступен'}")
        
        with col3:
            st.subheader(f"🤖 {other_player['name']}")
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.metric("💰 Фишки", other_player['chips'])
            with col_b:
                st.metric("⭐ Очки", other_player['score'])
            with col_c:
                st.metric("📦 Контейнеров", len(other_player['containers']))
            
            if other_player['containers']:
                st.write("**Контейнеры соперника:**")
                for c in other_player['containers']:
                    st.write(f"{get_emoji(c)} {c} (+{VALUES.get(c, 0)})")
            else:
                st.write("*У соперника пока нет контейнеров*")
        
        # Действия
        st.divider()
        st.subheader("🎯 Ваши действия")
        
        if not game.game_over and game.current_round <= MAX_ROUNDS:
            available = game.get_available_containers()
            
            if game.auction_active:
                # Аукцион
                st.markdown("""
                <div class="auction-box">
                    <h3>🔥 ИДЕТ АУКЦИОН!</h3>
                </div>
                """, unsafe_allow_html=True)
                
                auction = game.auction_data
                container = next((c for c in game.containers if c['id'] == auction['container_id']), None)
                
                if container:
                    st.write(f"**📦 Контейнер:** ❓ (содержимое скрыто)")
                    st.write(f"**💰 Текущая ставка:** {auction['current_price']} фишек")
                    st.write(f"**🔄 Повышений:** {auction['raise_count']}/{AUCTION_MAX_RAISES}")
                    
                    if st.session_state.player_id not in auction['passed']:
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            if st.button("⬆️ Повысить", use_container_width=True):
                                game.auction_bid(st.session_state.player_id, 'raise')
                                st.rerun()
                        with col2:
                            if st.button("🙅 Пас", use_container_width=True):
                                game.auction_bid(st.session_state.player_id, 'pass')
                                st.rerun()
                        with col3:
                            if st.button("💰 Купить сейчас", use_container_width=True):
                                game.auction_bid(st.session_state.player_id, 'buy')
                                st.rerun()
                    else:
                        st.warning("Вы уже пасовали в этом аукционе")
            
            elif available:
                st.write("📦 **Доступные контейнеры (содержимое неизвестно!):**")
                
                # Показываем контейнеры в сетке
                cols = st.columns(min(3, len(available)))
                for idx, container in enumerate(available):
                    col = cols[idx % 3]
                    with col:
                        with st.container():
                            st.markdown(f"""
                            <div style='border: 2px solid #ddd; border-radius: 10px; padding: 15px; margin: 5px; text-align: center; background: white;'>
                                <div style='font-size: 40px;'>❓</div>
                                <div style='font-size: 20px; font-weight: bold;'>💰 {container['price']} фишек</div>
                                <div style='font-size: 12px; color: #888;'>Содержимое скрыто 🔒</div>
                            </div>
                            """, unsafe_allow_html=True)
                            
                            btn_col1, btn_col2 = st.columns(2)
                            with btn_col1:
                                if st.button(f"💰 Купить", key=f"buy_{container['id']}", use_container_width=True):
                                    game.buy_container(st.session_state.player_id, container['id'])
                                    st.rerun()
                            with btn_col2:
                                if st.button(f"🔍 Рентген", key=f"xray_{container['id']}", use_container_width=True, 
                                           disabled=current_player['used_xray']):
                                    game.use_xray(st.session_state.player_id, container['id'])
                                    st.rerun()
            else:
                st.info("✅ Все контейнеры куплены!")
            
            # Дополнительные действия
            st.divider()
            col1, col2, col3 = st.columns(3)
            
            with col1:
                if st.button("🦅 Перехват", use_container_width=True, disabled=current_player['used_intercept']):
                    game.use_intercept(st.session_state.player_id)
                    st.rerun()
            
            with col2:
                if st.button("⏭️ Пропустить ход", use_container_width=True):
                    game.message = f"{current_player['name']} пропустил ход"
                    st.rerun()
            
            with col3:
                # Принудительное завершение раунда (для тестирования)
                if st.button("⏰ Завершить раунд", use_container_width=True):
                    available_containers = game.get_available_containers()
                    for c in available_containers:
                        c['bought'] = True
                    game.message = "⏰ Раунд принудительно завершен!"
                    st.rerun()
            
            # Таймер
            if not game.game_over:
                elapsed = time.time() - game.round_start_time
                remaining = max(0, ROUND_TIMEOUT - elapsed)
                st.progress(remaining / ROUND_TIMEOUT)
                st.caption(f"⏱️ Осталось времени: {int(remaining)} секунд")
        
        # Завершение раунда
        if game.check_round_end() and not game.game_over and game.current_round < MAX_ROUNDS:
            st.divider()
            if st.button("➡️ Следующий раунд", use_container_width=True, type="primary"):
                game.next_round()
                st.rerun()
        
        # Проверка окончания игры
        if game.check_game_over():
            st.balloons()
            st.success(game.message)
            
            st.divider()
            st.subheader("🏆 ФИНАЛЬНЫЕ РЕЗУЛЬТАТЫ")
            
            col1, col2 = st.columns(2)
            with col1:
                p1 = game.players['player1']
                st.markdown(f"""
                <div style='border: 2px solid #4CAF50; border-radius: 10px; padding: 15px;'>
                    <h3>👤 {p1['name']}</h3>
                    <p>📦 Контейнеров: {len(p1['containers'])}</p>
                    <p>⭐ Очки: {p1['score']}</p>
                    <p>💰 Фишки: {p1['chips']}</p>
                    <p>📦 Контейнеры: {', '.join(p1['containers']) if p1['containers'] else 'нет'}</p>
                </div>
                """, unsafe_allow_html=True)
            
            with col2:
                p2 = game.players['player2']
                st.markdown(f"""
                <div style='border: 2px solid #4CAF50; border-radius: 10px; padding: 15px;'>
                    <h3>👤 {p2['name']}</h3>
                    <p>📦 Контейнеров: {len(p2['containers'])}</p>
                    <p>⭐ Очки: {p2['score']}</p>
                    <p>💰 Фишки: {p2['chips']}</p>
                    <p>📦 Контейнеры: {', '.join(p2['containers']) if p2['containers'] else 'нет'}</p>
                </div>
                """, unsafe_allow_html=True)
            
            if st.button("🔄 Новая игра", use_container_width=True, type="primary"):
                game.reset()
                st.session_state.game_started = False
                st.session_state.player_id = None
                st.rerun()

# ---------------------------
# 4. ЗАПУСК
# ---------------------------

if __name__ == "__main__":
    main()
