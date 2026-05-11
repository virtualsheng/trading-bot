"""
Opening Range Breakout (ORB) Strategy
- Defines opening range using first 15 minutes (3 x 5-min bars)
- Enters on 5-min close above/below the range
- Stop loss at midpoint of opening range
- Target = 2x the risk (2:1 reward:risk)
- Exits at end of day if not stopped out

Trades TQQQ for long breakouts, SQQQ for short breakdowns.
"""

import pandas as pd
from lumibot.strategies import Strategy

class ORBStrategy(Strategy):
    # ... (Keep your existing strategy logic here) ...

    def on_strategy_end(self):
        # 1. Python 3.12 Crash Fix
        if hasattr(self, '_strategy_returns_df') and self._strategy_returns_df is not None:
            try:
                self._strategy_returns_df.index = pd.to_datetime(self._strategy_returns_df.index).view('int64') // 10**9
            except Exception:
                pass

        print("\n" + "="*60)
        print("📊 FINAL PERFORMANCE SUMMARY")
        print("="*60)

        # 2. Get trades from the executor if the method doesn't exist
        executor = getattr(self, '_strategy_executor', None)
        trades = getattr(executor, 'trades', []) if executor else []
        
        if not trades and hasattr(self, 'get_trades'):
            trades = self.get_trades()

        if trades:
            wins = [t for t in trades if getattr(t, 'pnl', 0) > 0]
            print(f"Total Trades: {len(trades)}")
            print(f"Win Rate:     {(len(wins)/len(trades))*100:.2f}%")
        else:
            print("❌ No trades executed.")
        print("="*60 + "\n")