# -*- coding: utf-8 -*-
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2018 The Electrum developers
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import copy
import os
import json
from typing import Sequence, Tuple, Mapping, Type, List, Optional

from .lntransport import LNPeerAddr
from .util import inv_dict, all_subclasses, classproperty
from . import bitcoin


def read_json(filename, default=None):
    path = os.path.join(os.path.dirname(__file__), filename)
    try:
        with open(path, 'r') as f:
            r = json.loads(f.read())
    except Exception:
        if default is None:
            # Sometimes it's better to hard-fail: the file might be missing
            # due to a packaging issue, which might otherwise go unnoticed.
            raise
        r = default
    return r


def create_fallback_node_list(fallback_nodes_dict: dict[str, dict]) -> List[LNPeerAddr]:
    """Take a json dict of fallback nodes like: k:node_id, v:{k:'host', k:'port'} and return LNPeerAddr list"""
    fallback_nodes = []
    for node_id, address in fallback_nodes_dict.items():
        fallback_nodes.append(
            LNPeerAddr(host=address['host'], port=int(address['port']), pubkey=bytes.fromhex(node_id)))
    return fallback_nodes


GIT_REPO_URL = "https://github.com/kutlusoy/elektron-net-electrum"
GIT_REPO_ISSUES_URL = "https://github.com/kutlusoy/elektron-net-electrum/issues"
RELEASE_NOTES_URL = "https://raw.githubusercontent.com/kutlusoy/elektron-net-electrum/refs/heads/main/RELEASE-NOTES"
BIP39_WALLET_FORMATS = read_json('bip39_wallet_formats.json')


class AbstractNet:

    NET_NAME: str
    TESTNET: bool
    WIF_PREFIX: int
    ADDRTYPE_P2PKH: int
    ADDRTYPE_P2SH: int
    SEGWIT_HRP: str
    BOLT11_HRP: str
    GENESIS: str
    BLOCK_HEIGHT_FIRST_LIGHTNING_CHANNELS: int = 0
    BIP44_COIN_TYPE: int
    LN_REALM_BYTE: int
    DEFAULT_PORTS: Mapping[str, str]
    LN_DNS_SEEDS: Sequence[str]
    XPRV_HEADERS: Mapping[str, int]
    XPRV_HEADERS_INV: Mapping[int, str]
    XPUB_HEADERS: Mapping[str, int]
    XPUB_HEADERS_INV: Mapping[int, str]

    @classmethod
    def max_checkpoint(cls) -> int:
        return max(0, len(cls.CHECKPOINTS) * 2016 - 1)

    @classmethod
    def rev_genesis_bytes(cls) -> bytes:
        return bytes.fromhex(cls.GENESIS)[::-1]

    @classmethod
    def set_as_network(cls) -> None:
        global net
        net = cls

    _cached_default_servers = None
    @classproperty
    def DEFAULT_SERVERS(cls) -> Mapping[str, Mapping[str, str]]:
        if cls._cached_default_servers is None:
            default_file = {} if cls.TESTNET else None  # for mainnet we hard-fail if the file is missing.
            d = read_json(os.path.join('chains', cls.NET_NAME, 'servers.json'), default_file)
            # sanity check
            for k, v in d.items():
                assert isinstance(v, dict), f'value for {k} not a dict in servers.json'
                assert all(isinstance(v2, str) for v2 in v.values()), f'non-str values for key {k} in servers.json'
            cls._cached_default_servers = d
        return copy.deepcopy(cls._cached_default_servers)

    _cached_fallback_lnnodes = None
    @classproperty
    def FALLBACK_LN_NODES(cls) -> Sequence[LNPeerAddr]:
        if cls._cached_fallback_lnnodes is None:
            default_file = {} if cls.TESTNET else None  # for mainnet we hard-fail if the file is missing.
            d = read_json(os.path.join('chains', cls.NET_NAME, 'fallback_lnnodes.json'), default_file)
            cls._cached_fallback_lnnodes = create_fallback_node_list(d)
        return cls._cached_fallback_lnnodes

    _cached_checkpoints = None
    @classproperty
    def CHECKPOINTS(cls) -> Sequence[Tuple[str, int]]:
        if cls._cached_checkpoints is None:
            default_file = [] if cls.TESTNET else None  # for mainnet we hard-fail if the file is missing.
            cls._cached_checkpoints = read_json(os.path.join('chains', cls.NET_NAME, 'checkpoints.json'), default_file)
        return cls._cached_checkpoints

    @classmethod
    def datadir_subdir(cls) -> Optional[str]:
        """The name of the folder in the filesystem.
        None means top-level, used by mainnet.
        """
        return cls.NET_NAME

    @classmethod
    def cli_flag(cls) -> str:
        """as used in e.g. `$ run_electrum --testnet4`"""
        return cls.NET_NAME

    @classmethod
    def config_key(cls) -> str:
        """as used for SimpleConfig.get()"""
        return cls.NET_NAME


class BitcoinMainnet(AbstractNet):
    """Elektron Net mainnet (ELEK). Values are sourced from elektron-net's
    src/kernel/chainparams.cpp (CMainParams) and src/wallet/walletutil.cpp
    (GenerateWalletDescriptor) -- treat those as ground truth for any future
    change here. See doc/elektron.md.
    """

    NET_NAME = "mainnet"
    TESTNET = False
    WIF_PREFIX = 0x80
    ADDRTYPE_P2PKH = 0
    ADDRTYPE_P2SH = 5
    # Bech32 HRP is Elektron Net's own ("be1q.../be1p..."); WIF/P2PKH/P2SH
    # bytes above are *intentionally* identical to Bitcoin mainnet (see
    # guideline-wallet-integration.md SS2.1/SS4.4) -- legacy Base58 addresses
    # and imported keys are valid on both chains. Any UI built on this fork
    # MUST default to Bech32 receive addresses and warn on legacy import;
    # not yet implemented here, tracked in doc/elektron.md.
    SEGWIT_HRP = "be"
    # BOLT11 HRP: finalized, matches SEGWIT_HRP by design, not a placeholder.
    # BOLT11 spec (bolts/11-payment-encoding.md): "prefix: ln + BIP-0173
    # currency prefix" -- i.e. the invoice HRP is always "ln" plus the
    # chain's own bech32 address HRP. Real precedent confirms this is the
    # convention actually followed, not just the literal spec text: Bitcoin
    # mainnet uses bc/bc (address/invoice), testnet tb/tb, regtest bcrt/bcrt
    # -- address HRP and invoice suffix are identical on every established
    # chain (only signet deliberately deviates, tbs vs tb, to disambiguate
    # from testnet since both otherwise share the same address HRP -- not
    # applicable here, Elektron Net's "be" is already unique). So "be" is
    # the correct, final Elektron Net mainnet BOLT11 HRP (full invoice
    # prefix: "lnbe"), not an undecided stand-in. See doc/elektron.md.
    BOLT11_HRP = SEGWIT_HRP
    GENESIS = "00000006b054338443f1a5d5534df21eab0d13232028158ae198edbb169f9dad"
    # DEFAULT_PORTS is Electrum's generic fallback (upstream default 't'/'s'
    # pair), used when a servers.json entry omits its own port. The one
    # live instance actually listed in chains/mainnet/servers.json,
    # electrs.elektron-net.org, is configured in elektron-net-stack
    # (install-elektron-stack.sh, electrs.toml template) with
    # electrum_rpc_addr = "0.0.0.0:50002" -- note this is plaintext, not
    # SSL, despite 50002 being the conventional SSL port number elsewhere;
    # electrs itself never terminates TLS (see elektron-net-electrs/doc/
    # config.md), so it is listed under the 't' key, not 's'.
    DEFAULT_PORTS = {'t': '50001', 's': '50002'}
    # No Lightning Network graph exists on Elektron Net yet (bootstrap
    # problem, guideline SS3.3/Phase 3) -- 0 disables LN-channel-related
    # historical scanning rather than implying a real activation height.
    BLOCK_HEIGHT_FIRST_LIGHTNING_CHANNELS = 0

    XPRV_HEADERS = {
        'standard':    0x0488ade4,  # xprv
        'p2wpkh-p2sh': 0x049d7878,  # yprv
        'p2wsh-p2sh':  0x0295b005,  # Yprv
        'p2wpkh':      0x04b2430c,  # zprv
        'p2wsh':       0x02aa7a99,  # Zprv
    }
    XPRV_HEADERS_INV = inv_dict(XPRV_HEADERS)
    XPUB_HEADERS = {
        'standard':    0x0488b21e,  # xpub
        'p2wpkh-p2sh': 0x049d7cb2,  # ypub
        'p2wsh-p2sh':  0x0295b43f,  # Ypub
        'p2wpkh':      0x04b24746,  # zpub
        'p2wsh':       0x02aa7ed3,  # Zpub
    }
    XPUB_HEADERS_INV = inv_dict(XPUB_HEADERS)
    # Elektron Net's registered SLIP-44 coin type (symbol ELEK), replacing
    # Bitcoin's 0'. See CHANGELOG-slip44-coin-type.md in elektron-net.
    # IMPORTANT: wallets created before 2026-07-15 derive at the legacy 0'
    # path and are NOT found by a default restore at this coin type -- a
    # MUST-level requirement (guideline SS3.1) not yet automated here; until
    # it is, restoring such a wallet requires manually specifying the 0'
    # derivation path (e.g. m/84'/0'/0') on restore.
    BIP44_COIN_TYPE = 1370
    # No Lightning realm byte has been registered for Elektron Net; 0 is a
    # placeholder (see BOLT11_HRP above), not a decision.
    LN_REALM_BYTE = 0
    # No Lightning routing/trampoline nodes exist on Elektron Net yet
    # (guideline SS3.3/Phase 3 bootstrap problem).
    LN_DNS_SEEDS = []

    @classmethod
    def datadir_subdir(cls):
        return None


class BitcoinTestnet(AbstractNet):

    NET_NAME = "testnet"
    TESTNET = True
    WIF_PREFIX = 0xef
    ADDRTYPE_P2PKH = 111
    ADDRTYPE_P2SH = 196
    SEGWIT_HRP = "tb"
    BOLT11_HRP = SEGWIT_HRP
    # Elektron Net testnet genesis (the "elektron testnet" chain) — matches
    # the fork's elektrond -testnet self-contained testnet as the wallet's
    # self-spins in docker via MM_LOCAL_TESTNET=1 (genesis check in
    # docker/start_testnet_infra.sh: GENESIS="00000078233832…c73e4f").
    GENESIS = "00000078233832d6ba39d7693ad96e9e6a8bc869a5f49cfaf4760f2c73c73e4f"
    DEFAULT_PORTS = {'t': '51001', 's': '51002'}

    XPRV_HEADERS = {
        'standard':    0x04358394,  # tprv
        'p2wpkh-p2sh': 0x044a4e28,  # uprv
        'p2wsh-p2sh':  0x024285b5,  # Uprv
        'p2wpkh':      0x045f18bc,  # vprv
        'p2wsh':       0x02575048,  # Vprv
    }
    XPRV_HEADERS_INV = inv_dict(XPRV_HEADERS)
    XPUB_HEADERS = {
        'standard':    0x043587cf,  # tpub
        'p2wpkh-p2sh': 0x044a5262,  # upub
        'p2wsh-p2sh':  0x024289ef,  # Upub
        'p2wpkh':      0x045f1cf6,  # vpub
        'p2wsh':       0x02575483,  # Vpub
    }
    XPUB_HEADERS_INV = inv_dict(XPUB_HEADERS)
    BIP44_COIN_TYPE = 1
    LN_REALM_BYTE = 1
    LN_DNS_SEEDS = [  # TODO investigate this again
        #'test.nodes.lightning.directory.',  # times out.
        #'lseed.bitcoinstats.com.',  # ignores REALM byte and returns mainnet peers...
    ]


class BitcoinTestnet4(BitcoinTestnet):

    NET_NAME = "testnet4"
    GENESIS = "00000000da84f2bafbbc53dee25a72ae507ff4914b867c565be350b0da8bf043"
    LN_DNS_SEEDS = []


class BitcoinRegtest(BitcoinTestnet):

    NET_NAME = "regtest"
    SEGWIT_HRP = "bcrt"
    BOLT11_HRP = SEGWIT_HRP
    GENESIS = "0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206"
    LN_DNS_SEEDS = []


class BitcoinSimnet(BitcoinTestnet):

    NET_NAME = "simnet"
    WIF_PREFIX = 0x64
    ADDRTYPE_P2PKH = 0x3f
    ADDRTYPE_P2SH = 0x7b
    SEGWIT_HRP = "sb"
    BOLT11_HRP = SEGWIT_HRP
    GENESIS = "683e86bd5c6d110d91b94b97137ba6bfe02dbbdb8e3dff722a669b5d69d77af6"
    LN_DNS_SEEDS = []


class BitcoinSignet(BitcoinTestnet):

    NET_NAME = "signet"
    BOLT11_HRP = "tbs"
    GENESIS = "00000008819873e925422c1ff0f99f7cc9bbb232af63a077a480a3633bee1ef6"
    LN_DNS_SEEDS = []


class BitcoinMutinynet(BitcoinTestnet):

    NET_NAME = "mutinynet"
    BOLT11_HRP = "tbs"
    GENESIS = "00000008819873e925422c1ff0f99f7cc9bbb232af63a077a480a3633bee1ef6"
    LN_DNS_SEEDS = []


NETS_LIST = tuple(all_subclasses(AbstractNet))  # type: Sequence[Type[AbstractNet]]
NETS_LIST = tuple(sorted(NETS_LIST, key=lambda x: x.NET_NAME))

assert len(NETS_LIST) == len(set([chain.NET_NAME for chain in NETS_LIST])), "NET_NAME must be unique for each concrete AbstractNet"
assert len(NETS_LIST) == len(set([chain.datadir_subdir() for chain in NETS_LIST])), "datadir must be unique for each concrete AbstractNet"
assert len(NETS_LIST) == len(set([chain.cli_flag() for chain in NETS_LIST])), "cli_flag must be unique for each concrete AbstractNet"
assert len(NETS_LIST) == len(set([chain.config_key() for chain in NETS_LIST])), "config_key must be unique for each concrete AbstractNet"

# don't import net directly, import the module instead (so that net is singleton)
net = BitcoinMainnet  # type: Type[AbstractNet]
