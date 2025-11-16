from config import *

import os.path
import logging
from datetime import datetime, timedelta
import webbrowser
import urllib.parse
import requests
import json

import pandas as pd


class ApiException(Exception):
    __module__ = 'builtins'


class TradeStation:

    def __init__(self, account: str, paper_trading: bool, key=api_key, secret=secret_key, user=ts_username):
        """base class to interface with the TS API"""

        self._s = requests.Session()
        self.config = {'client_id': key,
                       'secret_key': secret,
                       'username': user,
                       'account': account,
                       'base_url': 'https://sim-api.tradestation.com/v3/' if paper_trading else 'https://api.tradestation.com/v3/',
                       'return_uri': 'http://localhost:3000'
                       }

    def _authenticate(self):
        """authenticate with TS and receive the refresh token"""

        # prepare the payload
        data = {'response_type': 'code',
                'client_id': self.config['client_id'],
                'audience': 'https://api.tradestation.com',
                'redirect_uri': self.config['return_uri'],
                'scope': 'openid offline_access MarketData ReadAccount Trade'
                }
        params = urllib.parse.urlencode(data)
        url = 'https://signin.tradestation.com/authorize?' + params

        # authenticate through the web
        webbrowser.open(url)
        access_code = input('Paste the access token: ')

        # prepare payload for refresh token
        data = {'grant_type': 'authorization_code',
                'client_id': self.config['client_id'],
                'client_secret': self.config['secret_key'],
                'code': access_code,
                'redirect_uri': self.config['return_uri']
                }

        access_response = requests.post('https://signin.tradestation.com/oauth/token',
                                        headers={'content-type': 'application/x-www-form-urlencoded'},
                                        data=data).json()

        # cache the refresh token
        with open('token_cache', 'w') as f:
            json.dump(access_response, f)

        print('Successfully authenticated and refresh token as been cached.')

    def _refresh(self):
        """refresh or obtain the access token"""

        data = {'grant_type': 'refresh_token',
                'client_id': self.config['client_id'],
                'client_secret': self.config['secret_key'],
                'refresh_token': self.config['refresh_token']
                }
        access_response = requests.post('https://signin.tradestation.com/oauth/token',
                                        headers={'content-type': 'application/x-www-form-urlencoded'},
                                        data=data).json()

        self.config['access_token'] = access_response['access_token']
        self.config['id_token'] = access_response['id_token']
        self.config['token_expiration'] = datetime.now() + timedelta(seconds=access_response['expires_in'] - 60)
        self._s.headers.update({'Authorization': 'Bearer ' + self.config['access_token']})

    def _request(self, method, path, headers=None, params=None, payload=None):
        """base handler for making requests"""

        # check if the refresh token is on file, if not, generate
        if not os.path.exists('token_cache'):
            self._authenticate()

        # load the refresh token
        if 'refresh_token' not in self.config:
            with open('token_cache', 'r') as f:
                token = json.load(f)
            self.config['refresh_token'] = token['refresh_token']

        # get access token
        if 'access_token' not in self.config or datetime.now() >= self.config['token_expiration']:
            self._refresh()

        resp = self._s.request(method, path, headers=headers, params=params, json=payload)

        if not resp:
            error_message = f'[{resp.status_code}] {resp.reason}'
            logging.error(error_message)
            raise ApiException(error_message)

        return resp

    def brokerage(self, action: str) -> dict:
        """get account information
        action: str, one of accounts, balances, bodbalances, historicalorders, orders, positions
        """

        # prepare the API endpoint url
        url = self.config['base_url'] + 'brokerage/accounts'
        if action != 'accounts':
            url = url + '/' + self.config['account'] + '/' + f'{action}'

        return self._request('GET', url).json()

    def get_bars(self, ticker: str, interval: int = None, unit: str = None, barsback: int = None, startdate: str = None,
                 sessiontemplate: str = None) -> pd.DataFrame:
        """get bars
        ticker: str, symbol
        interval: int, number of units, should be 1 for anything non-minute
        unit: str, units of interval one of Minute, Daily, Weekly, Monthly
        barsback: int, number of bars to deliver
        startdate: str, optional paramater for strftime, default None will start at current time
        sesstiontemplate: str, specify if pre or after market is included default None is during market
        """

        # prepare the API endpoint url
        url = self.config['base_url'] + 'marketdata/barcharts/' + f'{ticker}'

        # prepare the payload
        params = {'interval': str(interval),
                   'unit': str(unit),
                   'barsback': str(barsback),
                   'startdate': str(startdate),
                   'sessiontemplate': str(sessiontemplate)
                  }
        params = {k: v for k, v in params.items() if v != 'None'}

        resp = self._request('GET', url, params=params)
        df = pd.DataFrame(resp.json()['Bars'])
        df.index = pd.to_datetime(df.TimeStamp)
        df = df.apply(pd.to_numeric, errors='ignore')

        return df

    def get_quote(self, ticker):
        """stream quotes
        ticker: str, symbol for instrument
        """

        # prepare the end point URL
        url = self.config['base_url'] + 'marketdata/quotes/' + ticker

        # get quote and return
        resp = self._request('GET', url)

        return json.loads(resp.content)

    def submit_order(self, ticker: str, quantity: int = None, trade_action: str = None, order_type: str = None,
                     time_in_force: str = None, trailing_stop: [float, int] = None) -> dict:
        """submit orders
        quantity: int, number of contracts to buy sell
        trade_action: str, must be BUY or SELL
        order_type: str, one of Limit, Market, StopMarket, StopLimit
        time_in_force: str, see TS WebAPI for full list, commonly DAY or GTC
        trailing_stop: float, percentage points (e.g., 2.00 for 2%)
        """

        # prepare the API endpoint url
        url = self.config['base_url'] + 'orderexecution/orders'

        # prepare the header
        headers = {"content-type": "application/json"}

        # prepare the payload
        payload = {'AccountID': self.config['account'],
                   'Symbol': str(ticker),
                   'Quantity': str(quantity),
                   'TradeAction': str.upper(trade_action),
                   'OrderType': str(order_type),
                   'TimeInForce': {'Duration': str(time_in_force)},
                   'Route': 'Intelligent'}
        if trailing_stop is not None:
            payload['AdvancedOptions'] = {'TrailingStop': {'Percent': str(round(trailing_stop, 2))}}

        resp = self._request('POST', url, headers=headers, payload=payload)

        return resp.json()

    def cancel_order(self, order_number: [int, str]) -> dict:
        """cancel open order
        order_number: int, order number from TS
        """

        # prepare the API endpoint url
        url = self.config['base_url'] + 'orderexecution/orders/' + f'{str(order_number).replace("-", "")}'

        resp = self._request('DELETE', url)

        return resp.json()

    def symbol_detail(self, ticker: str) -> dict:
        """fetch ticker detail"""

        # prepare the API endpoint url
        url = self.config['base_url'] + 'marketdata/symbols/' + ticker

        resp = self._request('GET', url)

        return resp.json()

    def check_margin(self) -> bool:
        """checks to see if there is safely enough cash relative to margin to enter a trade"""

        # get the initial margin on the account
        account = self.brokerage('balances')
        buying_power = float(account['Balances'][0]['BuyingPower'])
        margin = float(account['Balances'][0]['BalanceDetail']['InitialMargin'])
        return buying_power > margin
