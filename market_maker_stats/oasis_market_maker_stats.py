# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2017 reverendus
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import datetime
import sys
from functools import reduce
from typing import List, Optional

import matplotlib.dates as md
import matplotlib.pyplot as plt
from matplotlib.dates import date2num
from web3 import Web3, HTTPProvider

from pymaker import Address
from pymaker.numeric import Wad
from pymaker.oasis import SimpleMarket, Order, LogMake, LogTake, LogKill


class State:
    def __init__(self, timestamp: int, order_book: List[Order], sai_address: Address, weth_address: Address):
        self.timestamp = timestamp
        self.order_book = order_book
        self.sai_address = sai_address
        self.weth_address = weth_address

    def closest_sell_price(self) -> Optional[Wad]:
        return min(self.sell_prices(), default=None)

    def furthest_sell_price(self) -> Optional[Wad]:
        return max(self.sell_prices(), default=None)

    def closest_buy_price(self) -> Optional[Wad]:
        return max(self.buy_prices(), default=None)

    def furthest_buy_price(self) -> Optional[Wad]:
        return min(self.buy_prices(), default=None)

    def sell_orders(self) -> List[Order]:
        return list(filter(lambda order: order.buy_which_token == self.sai_address and
                                         order.sell_which_token == self.weth_address, self.order_book))

    def sell_prices(self) -> List[Wad]:
        return list(map(lambda order: order.buy_to_sell_price, self.sell_orders()))

    def buy_orders(self) -> List[Order]:
        return list(filter(lambda order: order.buy_which_token == self.weth_address and
                                         order.sell_which_token == self.sai_address, self.order_book))

    def buy_prices(self) -> List[Wad]:
        return list(map(lambda order: order.sell_to_buy_price, self.buy_orders()))


class OasisMarketMakerStats:
    """Tool to analyze the OasisDEX Market Maker keeper performance."""

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='bite-keeper')
        parser.add_argument("--rpc-host", help="JSON-RPC host (default: `localhost')", default="localhost", type=str)
        parser.add_argument("--rpc-port", help="JSON-RPC port (default: `8545')", default=8545, type=int)
        parser.add_argument("--oasis-address", help="Ethereum address of the OasisDEX contract", required=True, type=str)
        parser.add_argument("--sai-address", help="Ethereum address of the SAI token", required=True, type=str)
        parser.add_argument("--weth-address", help="Ethereum address of the WETH token", required=True, type=str)
        parser.add_argument("--market-maker-address", help="Ethereum account of the market maker to analyze", required=True, type=str)
        parser.add_argument("--past-blocks", help="Number of past blocks to analyze", required=True, type=int)
        self.arguments = parser.parse_args(args)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}"))
        self.sai_address = Address(self.arguments.sai_address)
        self.weth_address = Address(self.arguments.weth_address)
        self.market_maker_address = Address(self.arguments.market_maker_address)
        self.otc = SimpleMarket(web3=self.web3, address=Address(self.arguments.oasis_address))

    def lifecycle(self):
        past_make = self.otc.past_make(self.arguments.past_blocks)
        past_take = self.otc.past_take(self.arguments.past_blocks)
        past_kill = self.otc.past_kill(self.arguments.past_blocks)

        def reduce_func(states, timestamp):
            if len(states) == 0:
                order_book = []
            else:
                order_book = states[-1].order_book

            # apply all LogMake events having this timestamp
            for log_make in filter(lambda log_make: log_make.timestamp == timestamp, past_make):
                order_book = self.apply_make(order_book, log_make)
                order_book = list(filter(lambda order: order.owner == self.market_maker_address, order_book))

            # apply all LogTake events having this timestamp
            for log_take in filter(lambda log_take: log_take.timestamp == timestamp, past_take):
                order_book = self.apply_take(order_book, log_take)

            # apply all LogKill events having this timestamp
            for log_kill in filter(lambda log_kill: log_kill.timestamp == timestamp, past_kill):
                order_book = self.apply_kill(order_book, log_kill)

            return states + [State(timestamp=timestamp,
                                   order_book=order_book,
                                   sai_address=self.sai_address,
                                   weth_address=self.weth_address)]

        event_timestamps = sorted(set(map(lambda event: event.timestamp, past_make + past_take + past_kill)))
        states = reduce(reduce_func, event_timestamps, [])
        self.draw(states)

    def draw(self, states: List[State]):
        plt.subplots_adjust(bottom=0.2)
        plt.xticks( rotation=25 )
        ax=plt.gca()
        xfmt = md.DateFormatter('%Y-%m-%d %H:%M:%S')
        ax.xaxis.set_major_formatter(xfmt)

        timestamps = list(map(lambda state: date2num(datetime.datetime.fromtimestamp(state.timestamp)), states))
        closest_sell_prices = list(map(lambda state: state.closest_sell_price(), states))
        furthest_sell_prices = list(map(lambda state: state.furthest_sell_price(), states))
        closest_buy_prices = list(map(lambda state: state.closest_buy_price(), states))
        furthest_buy_prices = list(map(lambda state: state.furthest_buy_price(), states))

        # plt.plot_date(timestamps, furthest_sell_prices, 'b:')
        # plt.plot_date(timestamps, furthest_buy_prices, 'g:')
        plt.plot_date(timestamps, closest_sell_prices, 'b--')
        plt.plot_date(timestamps, closest_buy_prices, 'g--')
        plt.show()

    def apply_make(self, order_book: List[Order], log_make: LogMake) -> List[Order]:
        return order_book + [Order(self.otc,
                                   order_id=log_make.order_id,
                                   sell_how_much=log_make.pay_amount,
                                   sell_which_token=log_make.pay_token,
                                   buy_how_much=log_make.buy_amount,
                                   buy_which_token=log_make.buy_token,
                                   owner=log_make.maker,
                                   timestamp=log_make.timestamp)]

    def apply_take(self, order_book: List[Order], log_take: LogTake):
        this_order = next(filter(lambda order: order.order_id == log_take.order_id, order_book), None)

        if this_order is not None:
            assert this_order.sell_which_token == log_take.pay_token
            assert this_order.buy_which_token == log_take.buy_token

            remaining_orders = list(filter(lambda order: order.order_id != log_take.order_id, order_book))
            this_order = Order(self.otc,
                               order_id=this_order.order_id,
                               sell_how_much=this_order.sell_how_much - log_take.take_amount,
                               sell_which_token=this_order.sell_which_token,
                               buy_how_much=this_order.buy_how_much - log_take.give_amount,
                               buy_which_token=this_order.buy_which_token,
                               owner=this_order.owner,
                               timestamp=this_order.timestamp)

            if this_order.sell_how_much > Wad(0) and this_order.buy_how_much > Wad(0):
                return remaining_orders + [this_order]
            else:
                return remaining_orders
        else:
            return order_book

    def apply_kill(self, order_book: List[Order], log_kill: LogKill) -> List[Order]:
        return list(filter(lambda order: order.order_id != log_kill.order_id, order_book))


if __name__ == '__main__':
    OasisMarketMakerStats(sys.argv[1:]).lifecycle()
