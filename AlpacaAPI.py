from collections import defaultdict
import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import TimeFrame, REST
from datetime import date, timedelta
import logging
import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AlpacaAPI")#="TradingBot"
logger.setLevel(logging.INFO)


class AlpacaAPI:
    def __init__(self, api_key, secret_key, base_url="https://paper-api.alpaca.markets"):
        """
        Alpaca API wrapper for trading and data fetching using alpaca-trade-api.
        """
        self.api = tradeapi.REST(api_key, secret_key, base_url)
        self.positions = {}  # Track current stocks
        self.checkbook = {}  # Track buy prices
        self.sold_book = {}  # History sold symbols

    from collections import defaultdict

    def populate_checkbook(self):
        """
        Fetch past buy orders and populate the checkbook with all buy prices for each symbol.
        """
        try:
            # Initialize checkbook as a defaultdict of lists
            if not isinstance(self.checkbook, defaultdict):
                self.checkbook = defaultdict(list)

            # Fetch all closed orders
            orders = self.api.list_orders(status='filled', limit=500)

            for order in orders:
                # Only process buy orders
                if order.side == 'buy' and order.filled_avg_price:
                    symbol = order.symbol
                    buy_price = float(order.filled_avg_price)

                    # Append buy price to the list for the symbol
                    if buy_price not in self.checkbook[symbol]:
                       self.checkbook[symbol].append(buy_price)
                       logging.info(f"Added {symbol} to checkbook with buy price {buy_price}")

        except Exception as e:
            logging.error(f"Error populating checkbook: {e}")


    def populate_sold_book(self):
        """
        Populate sold_book with past sell transactions.
        """
        transactions = self.fetch_all_transactions()
        for txn in transactions:
            if txn['side'] == 'sell' and txn['price'] is not None:
                self.sold_book[txn['symbol']] = {
                    'sell_price': txn['price'],
                    'timestamp': txn['timestamp']
                }
                logging.info(f"Added {txn['symbol']} to sold_book with price {txn['price']}")


    def fetch_positions(self):
        """
        Fetch current positions from Alpaca API, including real-time market prices.
        """
        try:
            # Fetch current positions
            positions = self.api.list_positions()

            # Store positions with additional real-time price info
            self.positions = {}
            for pos in positions:
                symbol = pos.symbol
                qty = int(pos.qty)
                current_price = float(pos.current_price)

                # Fetch the latest market price (real-time data)
                try:
                    latest_trade = self.api.get_latest_trade(symbol=symbol, feed='iex')
                    market_price = float(latest_trade.price)  # Real-time price
                except Exception as e:
                    market_price = current_price  # Fallback to Alpaca's current_price
                    logger.warning(f"Error fetching real-time price for {symbol}: {e}")

                self.positions[symbol] = {
                    'qty': int(qty),
                    'current_price': float(current_price),  # From Alpaca position data
                    'market_price': float(market_price)    # Real-time market price
                }
            self.populate_checkbook()
            return self.positions
        except tradeapi.rest.APIError as e:
            raise Exception(f"Error fetching positions: {e}")


    def place_order(self, symbol, qty, side="buy", order_type="market", time_in_force="gtc"):
        """
        Place an order via Alpaca API.
        """
        try:
            order = self.api.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                type=order_type,
                time_in_force=time_in_force,
            )
            print(f"Order placed: {order}")
        except tradeapi.rest.APIError as e:
            raise Exception(f"Error placing order: {e}")

    def calculate_portfolio_value(self):
        """
        Calculate the total portfolio value.
        """
        try:
            account = self.api.get_account()
            return float(account.portfolio_value)
        except tradeapi.rest.APIError as e:
            raise Exception(f"Error fetching portfolio value: {e}")

    def fetch_historical_data(self, symbol, start_date):
        try:
            end_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
            bars = self.api.get_bars(
                symbol,
                TimeFrame.Day,
                start=start_date,
                end=end_date,
                adjustment='all'
            ).df
            #logging.info(f"Data retrieved: {bars.tail(3)}")
            return bars
        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {e}")
            raise

    
    def fetch_raw_data(self, symbol):
        """
        Fetches the latest bar data for a specific symbol.
        Returns a dictionary with relevant fields like open, high, low, close, volume.
        """
        try:
            latest_bar = self.api.get_latest_bar(symbol=symbol, feed='iex')  # Fetch data
            # Format the data as a dictionary
            return {
                'open': latest_bar.o,
                'high': latest_bar.h,
                'low': latest_bar.l,
                'close': latest_bar.c,
                'volume': latest_bar.v,
                'timestamp': latest_bar.t
            }
        except Exception as e:
            logger.error(f"Error fetching raw data for {symbol}: {e}")
            return None

    
    
    def fetch_all_transactions(self, status='filled', limit=200):
        """
        Fetch all filled transactions from Alpaca.
        """
        try:
            orders = self.api.list_orders(status=status, limit=limit)
            transaction_data = []
            for order in orders:
                transaction_data.append({
                    'symbol': order.symbol,
                    'side': order.side,
                    'price': float(order.filled_avg_price) if order.filled_avg_price else None,
                    'qty': int(order.filled_qty) if order.filled_qty else None,
                    'timestamp': order.filled_at
                })
            return transaction_data
        except Exception as e:
            logger.error(f"Error fetching transactions: {e}")
            return []


    def is_market_open(self):
        """
        Check if the market is currently open.
        """
        try:
            clock = self.api.get_clock()
            return clock.is_open
        except tradeapi.rest.APIError as e:
            raise Exception(f"Error checking market status: {e}")

    def get_account_info(self):
        """
        Retrieve account details.
        """
        try:
            return self.api.get_account()
        except tradeapi.rest.APIError as e:
            raise Exception(f"Error fetching account information: {e}")


# Example usage
if __name__ == "__main__":
    from config import ALPACA_API_KEY, ALPACA_SECRET_KEY

    alpaca = AlpacaAPI(ALPACA_API_KEY, ALPACA_SECRET_KEY)

    # # Fetch positions
    # positions = alpaca.fetch_positions()
    # print("Positions:", positions)

    # # Place an order
    # try:
    #     alpaca.place_order("RGTI", 1, side="buy")
    # except Exception as e:
    #     print(f"Error placing order: {e}")

    # Fetch historical data
    # data = alpaca.fetch_historical_data("RGTI", "2024-11-01")
    # print(data)
    # print("\n\n")
    # raw_data = alpaca.api.get_latest_bars("RGTI")
    # print(raw_data)

    # # Check if market is open
    # is_open = alpaca.is_market_open()
    # print("\nMarket is open:", is_open)
    # print("\n", alpaca.calculate_portfolio_value())
