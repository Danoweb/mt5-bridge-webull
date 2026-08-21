//+------------------------------------------------------------------+
//|                                         MT5WebullBridgeEA.mq5     |
//|                                                                    |
//| Mirrors trades opened/closed on this MT5 chart to the Python      |
//| bridge service, which places the equivalent order on Webull.      |
//|                                                                    |
//| HOW IT WORKS                                                       |
//| MT5 is used here purely as the strategy/execution engine (charts, |
//| indicators, your EA logic, or manual trading) while the actual    |
//| broker fill happens on Webull. Whenever this MT5 account's net    |
//| position in a symbol changes, we compute the delta and send it to |
//| the bridge as an order request. When a position is fully closed   |
//| on MT5, we tell the bridge to close the matching Webull position. |
//|                                                                    |
//| SETUP                                                               |
//| 1. Tools > Options > Expert Advisors > "Allow WebRequest for       |
//|    listed URL" and add your bridge's URL (see README).             |
//| 2. Set the BridgeUrl and ApiKey inputs below to match your          |
//|    deployment.                                                     |
//| 3. Attach this EA to every chart/symbol you want mirrored, with a  |
//|    distinct MagicNumber per strategy if you run more than one.     |
//+------------------------------------------------------------------+
#property copyright "mt5-bridge-webull"
#property version   "1.00"
#property strict

//--- Inputs -----------------------------------------------------------
input string   BridgeUrl          = "https://your-bridge-domain.example.com"; // Base URL of the bridge (no trailing slash)
input string   ApiKey             = "";      // Must match BRIDGE_API_KEY on the bridge
input double   LotToShareFactor   = 100.0;   // Shares sent to Webull per 1.0 MT5 lot for this symbol
input int      MagicNumber        = 990011;  // Only positions with this magic are mirrored
input int      HttpTimeoutMs      = 5000;    // WebRequest timeout
input int      RetryIntervalSecs  = 15;      // How often the timer retries queued failed sends
input int      MaxRetryQueueSize  = 50;      // Safety cap so a persistent outage can't grow this unbounded

//--- Tracks the last-known mirrored volume per symbol so we can compute
//    deltas when a position changes, rather than resending the full size
//    every tick (which would double-count on partial adds).
string   g_trackedSymbols[];
double   g_trackedVolume[];   // signed: positive = net long, negative = net short

//--- A tiny retry queue for requests that failed to send (e.g. transient
//    network blip between this machine and a cloud/home-hosted bridge).
//    We do NOT retry forever or silently drop a failed trade mirror --
//    every entry here represents a real Webull-side action that hasn't
//    happened yet, and OnTimer keeps trying until it succeeds or the
//    queue cap is hit (at which point we alert loudly rather than lose
//    the signal silently).
string   g_retryQueueBody[];
string   g_retryQueueEndpoint[];
string   g_retryQueueMethod[];

//+------------------------------------------------------------------+
//| Expert initialization                                              |
//+------------------------------------------------------------------+
int OnInit()
  {
   if(ApiKey == "")
     {
      Alert("MT5WebullBridgeEA: ApiKey input is empty. Set it before trading.");
      return(INIT_PARAMETERS_INCORRECT);
     }

   // Fail fast if the bridge isn't reachable/whitelisted, instead of only
   // discovering it the first time a real trade needs to be mirrored.
   string response;
   int status = HttpRequest("GET", "/health", "", response);
   if(status != 200)
     {
      Alert(StringFormat(
         "MT5WebullBridgeEA: could not reach bridge at %s/health (HTTP %d). "
         "Check the URL is correct and whitelisted under Tools>Options>Expert Advisors.",
         BridgeUrl, status));
      // We intentionally still allow the EA to initialize (return success)
      // rather than blocking chart attachment entirely -- a temporarily
      // unreachable bridge shouldn't prevent MT5-side strategy logic from
      // running; the retry queue will pick up mirrored trades once the
      // bridge comes back.
     }
   else
     {
      PrintFormat("MT5WebullBridgeEA: bridge reachable at %s (%s)", BridgeUrl, response);
     }

   EventSetTimer(RetryIntervalSecs);
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization                                            |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

//+------------------------------------------------------------------+
//| Fires whenever a deal/order/position changes on this account.      |
//| This is where we detect trades to mirror.                          |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                         const MqlTradeRequest &request,
                         const MqlTradeResult &result)
  {
   // Only act on completed deals (an actual fill), not on every order
   // state change (pending order placed, modified, etc.) -- those don't
   // represent a change in our net position yet.
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD)
      return;

   if(!HistoryDealSelect(trans.deal))
      return;

   long dealMagic = HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
   if(dealMagic != MagicNumber)
      return; // Not one of ours -- don't mirror trades from other EAs/manual trading with a different magic.

   string symbol = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
   SyncSymbolPosition(symbol);
  }

//+------------------------------------------------------------------+
//| Compares MT5's current net position in `symbol` to what we last    |
//| mirrored, and sends the delta (or a close) to the bridge.          |
//+------------------------------------------------------------------+
void SyncSymbolPosition(string symbol)
  {
   double currentVolume = GetNetPositionVolume(symbol);
   double lastVolume = GetTrackedVolume(symbol);
   double delta = currentVolume - lastVolume;

   // Nothing changed for this symbol (e.g. the deal was for a different
   // symbol's position, or was fully offset) -- avoid sending a no-op
   // order to the bridge.
   if(MathAbs(delta) < 0.0000001)
      return;

   if(currentVolume == 0.0 && lastVolume != 0.0)
     {
      // Position fully closed on MT5 -> close the mirrored Webull position
      // outright, rather than sending a delta order, so rounding in
      // LotToShareFactor can never leave a small leftover position on
      // Webull.
      CloseSymbolOnBridge(symbol);
     }
   else
     {
      string side = (delta > 0) ? "BUY" : "SELL";
      double shareQty = MathAbs(delta) * LotToShareFactor;
      SendOrderToBridge(symbol, side, shareQty);
     }

   SetTrackedVolume(symbol, currentVolume);
  }

//+------------------------------------------------------------------+
//| Net open volume (lots, signed) for `symbol` across positions with |
//| our MagicNumber. MT5 nets positions per symbol in netting-mode      |
//| accounts; for hedging-mode accounts this sums all matching tickets. |
//+------------------------------------------------------------------+
double GetNetPositionVolume(string symbol)
  {
   double total = 0.0;
   for(int i = 0; i < PositionsTotal(); i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket))
         continue;
      if(PositionGetString(POSITION_SYMBOL) != symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;

      double vol = PositionGetDouble(POSITION_VOLUME);
      long ptype = PositionGetInteger(POSITION_TYPE);
      total += (ptype == POSITION_TYPE_BUY) ? vol : -vol;
     }
   return total;
  }

//+------------------------------------------------------------------+
//| Send a market order request to the bridge for `symbol`.            |
//+------------------------------------------------------------------+
void SendOrderToBridge(string symbol, string side, double quantity)
  {
   string clientOrderId = StringFormat("mt5-%d-%s-%d", MagicNumber, symbol, GetTickCount());
   string body = StringFormat(
      "{\"symbol\":\"%s\",\"side\":\"%s\",\"quantity\":%s,\"order_type\":\"MARKET\",\"time_in_force\":\"DAY\",\"client_order_id\":\"%s\"}",
      symbol, side, DoubleToString(quantity, 4), clientOrderId);

   string response;
   int status = HttpRequest("POST", "/orders", body, response);
   if(status != 200 && status != 201)
     {
      PrintFormat("MT5WebullBridgeEA: order send FAILED (HTTP %d): %s -- queuing for retry", status, response);
      EnqueueRetry("/orders", "POST", body);
     }
   else
     {
      PrintFormat("MT5WebullBridgeEA: mirrored %s %s x%.4f -> %s", side, symbol, quantity, response);
     }
  }

//+------------------------------------------------------------------+
//| Tell the bridge to flatten its Webull position for `symbol`.       |
//+------------------------------------------------------------------+
void CloseSymbolOnBridge(string symbol)
  {
   string endpoint = StringFormat("/positions/%s/close", symbol);
   string response;
   int status = HttpRequest("POST", endpoint, "", response);
   if(status != 200 && status != 201 && status != 404)
     {
      // 404 means the bridge already had no open position for this symbol
      // (e.g. it was closed by a previous retry) -- that's fine, not an
      // error worth queuing a retry for.
      PrintFormat("MT5WebullBridgeEA: close position FAILED (HTTP %d): %s -- queuing for retry", status, response);
      EnqueueRetry(endpoint, "POST", "");
     }
   else
     {
      PrintFormat("MT5WebullBridgeEA: closed Webull position for %s -> %s", symbol, response);
     }
  }

//+------------------------------------------------------------------+
//| Periodic timer: retries any queued failed sends.                   |
//+------------------------------------------------------------------+
void OnTimer()
  {
   int n = ArraySize(g_retryQueueBody);
   if(n == 0)
      return;

   PrintFormat("MT5WebullBridgeEA: retrying %d queued bridge request(s)", n);

   // Iterate a snapshot and rebuild the queue with only the still-failing
   // entries, so a request that succeeds on retry doesn't get retried
   // again next tick.
   string stillFailedBody[], stillFailedEndpoint[], stillFailedMethod[];
   for(int i = 0; i < n; i++)
     {
      string response;
      int status = HttpRequest(g_retryQueueMethod[i], g_retryQueueEndpoint[i], g_retryQueueBody[i], response);
      bool ok = (status == 200 || status == 201 || status == 404);
      if(!ok)
        {
         int sz = ArraySize(stillFailedBody);
         ArrayResize(stillFailedBody, sz + 1);
         ArrayResize(stillFailedEndpoint, sz + 1);
         ArrayResize(stillFailedMethod, sz + 1);
         stillFailedBody[sz] = g_retryQueueBody[i];
         stillFailedEndpoint[sz] = g_retryQueueEndpoint[i];
         stillFailedMethod[sz] = g_retryQueueMethod[i];
        }
     }

   ArrayFree(g_retryQueueBody);
   ArrayFree(g_retryQueueEndpoint);
   ArrayFree(g_retryQueueMethod);
   g_retryQueueBody = stillFailedBody;
   g_retryQueueEndpoint = stillFailedEndpoint;
   g_retryQueueMethod = stillFailedMethod;

   if(ArraySize(g_retryQueueBody) > 0)
      PrintFormat("MT5WebullBridgeEA: %d request(s) still failing after retry", ArraySize(g_retryQueueBody));
  }

void EnqueueRetry(string endpoint, string method, string body)
  {
   int n = ArraySize(g_retryQueueBody);
   if(n >= MaxRetryQueueSize)
     {
      // We would rather alert loudly than silently grow forever or
      // silently drop the oldest entry -- a full retry queue means the
      // bridge has been unreachable long enough that a human needs to
      // look at it (MT5 and Webull positions may now be diverging).
      Alert("MT5WebullBridgeEA: retry queue is full! Bridge has been unreachable too long -- check it manually.");
      return;
     }
   ArrayResize(g_retryQueueBody, n + 1);
   ArrayResize(g_retryQueueEndpoint, n + 1);
   ArrayResize(g_retryQueueMethod, n + 1);
   g_retryQueueBody[n] = body;
   g_retryQueueEndpoint[n] = endpoint;
   g_retryQueueMethod[n] = method;
  }

//+------------------------------------------------------------------+
//| Tracked-volume helpers (simple parallel-array map since MQL5 has   |
//| no built-in associative container).                                |
//+------------------------------------------------------------------+
double GetTrackedVolume(string symbol)
  {
   int n = ArraySize(g_trackedSymbols);
   for(int i = 0; i < n; i++)
      if(g_trackedSymbols[i] == symbol)
         return g_trackedVolume[i];
   return 0.0;
  }

void SetTrackedVolume(string symbol, double volume)
  {
   int n = ArraySize(g_trackedSymbols);
   for(int i = 0; i < n; i++)
     {
      if(g_trackedSymbols[i] == symbol)
        {
         g_trackedVolume[i] = volume;
         return;
        }
     }
   ArrayResize(g_trackedSymbols, n + 1);
   ArrayResize(g_trackedVolume, n + 1);
   g_trackedSymbols[n] = symbol;
   g_trackedVolume[n] = volume;
  }

//+------------------------------------------------------------------+
//| Thin wrapper around WebRequest(): builds the full URL/headers,     |
//| sends the request, and returns the HTTP status code (or -1 on a    |
//| WebRequest-level failure, e.g. URL not whitelisted).                |
//+------------------------------------------------------------------+
int HttpRequest(string method, string path, string body, string &responseOut)
  {
   string url = BridgeUrl + path;
   string headers = "Content-Type: application/json\r\nX-API-Key: " + ApiKey + "\r\n";
   char postData[];
   if(StringLen(body) > 0)
      StringToCharArray(body, postData, 0, StringLen(body), CP_UTF8);

   char result[];
   string responseHeaders;

   ResetLastError();
   int status = WebRequest(method, url, headers, HttpTimeoutMs, postData, result, responseHeaders);

   if(status == -1)
     {
      int err = GetLastError();
      responseOut = StringFormat("WebRequest failed, error %d (is the URL whitelisted?)", err);
      return -1;
     }

   responseOut = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   return status;
  }
//+------------------------------------------------------------------+
