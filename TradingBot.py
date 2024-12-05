import signal
import json
import numpy as np
import requests
import AlpacaAPI
from AlpacaAPI import *
import asyncio
import websockets
import pandas as pd
import threading
from config import ALPACA_API_KEY
from config import ALPACA_SECRET_KEY
import logging
import sys
import alpaca_trade_api as trade_api
from datetime import date
#import backtesting
    
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)#="TradingBot"
logger.setLevel(logging.DEBUG)

class TradingBot:
    def __init__(self, alpaca_api, datastream_uri):
        """
        Initialize TradingBot with Alpaca API keys and create an Alpaca API instance.
        """
        self.alpaca = alpaca_api
        self.running = True
        self.lock = threading.Lock()
        self.queue = asyncio.Queue(maxsize=1000)  # Queue for sharing data_update output.
        self.datastream = Datastream(datastream_uri) #datastream instance for websockets
        self.checkbook = {}  # Track buy prices for symbols # <--- To be Implemented

    def is_market_open(self):
        try:
            api = trade_api.REST(ALPACA_API_KEY, ALPACA_SECRET_KEY, base_url="https://paper-api.alpaca.markets")
            clock = api.get_clock()
            return clock.is_open
        except Exception as e:
            logger.error(f"Error checking market status: {e}")
            return False



    async def update_live_data(self):
        while self.running:
            if not self.is_market_open():
                logger.info("Market is closed. Skipping live data updates...")  
                asyncio.sleep(60)  # loop condition, as averse to 'return'
                continue  
            
            try:
                # Fetch positions
                self.alpaca.fetch_positions()
                logger.info("Fetched positions succesfully")
            except Exception as e:
                logger.error(f"Error fetching live data: {e}")

            await asyncio.sleep(30)


    async def purge_queue(self):
        while self.running:
            try:
                while not self.queue.empty():
                    await self.queue.get_nowait()  # Clear one item
                await asyncio.sleep(60)  # Run every 60 seconds
            except Exception as e:
                logger.error(f"Error purging queue: {e}")

               
    def moving_average_crossover(self, symbol):
        """
        Strategy: Generate buy (1) and sell (-1) signals based on moving average crossover.
        """
        data = self.alpaca.fetch_historical_data(symbol, "2024-10-01")
        signals = []
        for i in range(len(data)):
            if data.iloc[i, 0] > data.iloc[i, 1]:
                signals.append(1)  # BUY signal
            elif data.iloc[i, 0] < data.iloc[i, 1]:
                signals.append(-1)  # SELL signal
            else:
                signals.append(0)  # No signal
        return np.array(signals)
        

    def backtest_strategy(self, symbol):
        """
        Backtests the moving average crossover strategy.
        Returns cumulative returns of the strategy.
        """
        """ 
        This provides the actual daily return for the asset.
            * signals: Multiplies the daily return by the corresponding signal to apply the trading strategy:
            A signal of 1 means you profit (or lose) based on the price movement.
            A signal of -1 means you gain if the price falls (short-selling).
            A signal of 0 means no position is taken, so the return is 0.
            .cumsum(): Computes the cumulative sum of the returns over time, reflecting the overall performance of the strategy.
            """
        execute = 0
        raw_data = self.alpaca.fetch_raw_data(symbol)

        # Moving Average Crossover
        signals = self.moving_average_crossover(symbol)
        returns = (raw_data['c'].pct_change() * signals).cumsum()
        if returns > 0 : execute += 1

        # Volatility
        self.backtest_volatility = lambda data, low, high: 1 if low <= (atr := self.calculate_volatility(data)) <= high else -1
        execute += self.backtest_volatility(data=raw_data, low=2, high=7)

        return execute > 0

    
    def execute_trades(self, signal, symbol):
        """
        Execute trades based on the signal. Signal:
        <>  1: Buy
        <> -1: Sell
        """
        with self.lock:
            self.alpaca.fetch_positions()
            logger.info(f"Processing signal {signal} for {symbol}")

            if signal == 1:  # BUY
                qty = 1
                logger.info(f"Placing BUY order for {symbol}")
                self.alpaca.place_order(symbol, qty=qty, side="buy")
                # Update Checkbook
                position = self.alpaca.positions.get(symbol)
                if position:
                    self.checkbook[symbol] = position[1]

            elif signal == -1:  # SELL
                logger.info(f"Placing SELL order for {symbol}")
                self.alpaca.place_order(symbol, qty=1, side="sell")
                #Update Checkbook
                if symbol in self.checkbook and self.checkbook[symbol]:
                    sold_price = self.checkbook[symbol].pop(len(self.checkbook[symbol])-1)   
                    logger.info(f"Sold {symbol} at {sold_price}")
                    if not self.checkbook[symbol]:  # If the list is now empty
                        del self.checkbook[symbol]

            print(f"Trade for {symbol} completed. Notifying other threads.")

    '''
    def evaluate_market_conditions(self, data, symbol):     # lets turn this into a nubmer from 1 to 10, and if it is above 8, it will be considered a highly volatile stock and should be dealt with differently
        """
        Analyze market trends and make action decisions.
        """
        short_avg = data['c'].rolling(20).mean().iloc[-1]
        long_avg = data['c'].rolling(50).mean().iloc[-1]
        current_price = data['c'].iloc[-1]
        atr = self.calculate_volatility(data) #incorporate this volatility in the future.

        stop_loss_price = self.calculate_stop_loss(entry_price=current_price, risk_threshold=0.05)

        # Decision logic
        if short_avg > long_avg and current_price > stop_loss_price:
            return "buy", 1
        if current_price < stop_loss_price:
            return "sell", self.alpaca.positions[symbol]
        elif short_avg < long_avg:
            if symbol in self.alpaca.positions and current_price < (self.alpaca.positions[symbol] * 0.95):
                return "sell", self.alpaca.positions[symbol]
        else:
            return None, 0
    '''

    async def monitor_market(self):
        """
        A method to monitor market conditions for all symbols.
        """  
        while self.running:
            try:
                positions = self.alpaca.fetch_positions()         
                if not positions:
                    logger.info("No positions to monitor")
                    asyncio.sleep(60)
                    continue

                for symbol, (qty,current_price) in positions.items():
                    logger.info(f"Monitoring {symbol}: qty={qty}, price={current_price}")

                    if symbol in self.checkbook:
                        buy_price = self.checkbook[symbol]
                        if current_price < self.calculate_stop_loss(buy_price): # sell if it is a loss
                            self.execute_trades(-1, symbol=symbol) 
                        elif self.backtest_strategy(symbol=symbol):             # buy if it is advantageous
                            if len(self.checkbook[symbol]) <= 10:               # limit number of stocks
                                self.execute_trades(1,symbol=symbol)   

                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Error monitoring market: {e}")

    '''
        async def forward_to_local_server(self):
        """
        Forward data to the local WebSocket server at ws://localhost:8080.
        """
        local_uri = "ws://localhost:8080"
        local_stream = Datastream(local_uri)
        try:
            if not await local_stream.connect_with_retries():
                logger.error("Unable to connect to the local WebSocket server.")
                return
            
            while self.running:
                try:
                    # Fetch data from the bot's queue
                    data = await self.queue.get()
                    
                    # Send data to the server
                    await local_stream.send_data(json.dumps(data))
                    logger.info(f"Data forwarded to local server: {data}")
                    
                    # Await server's response (optional)
                    response = await local_stream.receive_data()
                    logger.info(f"Response from server: {response}")
                except Exception as e:
                    await asyncio.sleep(.5)
                    logger.error(f"Error forwarding data: {e}")
        except Exception as e:
            logger.error(f"Error connecting to local server: {e}")
        finally:        
            await local_stream.close()
    '''

    async def health_check(self):
        """
        Periodically checks if the bot is functioning correctly.
        If not, gracefully shuts down or restarts the bot.
        """
        while self.running:
            try:
                # Example checks
                if not self.datastream.connection:
                    logger.warning("WebSocket disconnected. Reconnecting...")
                    await self.datastream.connect_with_retries()
                
                # Add more health checks (e.g., queue size, Alpaca API availability)
                if self.queue.qsize() > 1000:
                    logger.warning("Queue size too large. Purging...")
                    await self.purge_queue()

                logger.info("Health check passed.")
            except Exception as e:
                logger.error(f"Health check failed: {e}")
                self.running = False  # Stop the bot to allow for external restart
            await asyncio.sleep(300)  # Run every 5 minutes


    async def run(self):
        """
        Starts the bot for all positions.
        """
        #positions = self.alpaca.fetch_positions()
        #symbols = list(positions.keys())
        tasks = [
            self.safe_task(self.update_live_data),    
            self.safe_task(self.monitor_market),
            self.safe_task(self.purge_queue),
            self.safe_task(self.health_check)
            ]
        await asyncio.gather(*tasks)
    

    async def safe_task(self, func, *args):
        try:
            await func(*args)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}")


    def calculate_volatility(self, data):
        """
        Calculate Average True Range (ATR) to measure volatility.
        """
        high_low = data['h'] - data['l']
        high_close = abs(data['h'] - data['c'].shift(1))
        low_close = abs(data['l'] - data['c'].shift(1))
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = true_range.rolling(14).mean()
        return atr.iloc[-1] #Latest ATR value 
    

        
    def calculate_position_value(self, symbol):
        """
        Calculate the value of a position (quantity * price).
        """
        qty = self.alpaca.positions.get(symbol)[0]
        price = self.alpaca.positions.get(symbol)[1]
        return qty * price * 0.98  # Adjust for slippage


    def calculate_stop_loss(self, entry_price, risk_threshold=0.05):
        """
        Calculate stop-loss price based on entry price and risk threshold.
        """
        stop_loss_price = entry_price * (1 - risk_threshold)
        print(f"Stop-loss set to {stop_loss_price}")
        return stop_loss_price
    
if __name__ == "__main__":

    async def signal_handler(signal, frame):
        bot.running = False
        logger.info("Shutting down the bot...")
        #bot.datastream.close()
        print(f"Portfolio value: {bot.alpaca.calculate_portfolio_value()}")
        await asyncio.run(bot.datastream.close())
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # START REAL ********
    alpaca = AlpacaAPI(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    datastream_uri = "wss://paper-api.alpaca.markets/stream"
    bot = TradingBot(alpaca, datastream_uri)
    asyncio.run(bot.datastream.connect())

    if not bot.is_market_open():
        logger.info("Market is closed. Exiting bot.")
        exit()

    asyncio.run(bot.run())
    print("In main(), post run()")
    # END REAL **********

"""
To add this additional logic to your bot, we need to integrate conditions for:

Monitor for Rebound/Rebuy Opportunities-
After selling, watch the stock for an upward trend or percentage recovery from its lowest price.
If the price rises again, issue a buy signal.

Increase positions-
One of the most important things that I have to implement is a method to determine whether or not
to buy more stock. I should have pull my portfolio value, my unallocated amount(probably math done by subtracting 
actively owned stock), and using moving average and something more advanced. Ideally the decision should go through
about 3 backtests with an affirmative result before a trade is executed

"""