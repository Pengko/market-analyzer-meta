PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS daily_ohlcv (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  open REAL,
  high REAL,
  low REAL,
  close REAL,
  pre_close REAL,
  change REAL,
  pct_chg REAL,
  vol REAL,
  amount REAL,
  PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS daily_basic (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  turnover_rate REAL,
  turnover_rate_f REAL,
  volume_ratio REAL,
  pe REAL,
  pe_ttm REAL,
  pb REAL,
  ps REAL,
  ps_ttm REAL,
  dv_ratio REAL,
  dv_ttm REAL,
  total_share REAL,
  float_share REAL,
  free_share REAL,
  total_mv REAL,
  circ_mv REAL,
  PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS stk_factor_pro (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  close REAL,
  pre_close REAL,
  pct_chg REAL,
  vol REAL,
  amount REAL,
  ma_bfq_5 REAL,
  ma_bfq_10 REAL,
  ma_bfq_20 REAL,
  ma_bfq_30 REAL,
  ma_bfq_60 REAL,
  rsi_bfq_6 REAL,
  rsi_bfq_12 REAL,
  rsi_bfq_24 REAL,
  volume_ratio REAL,
  turnover_rate_f REAL,
  total_mv REAL,
  circ_mv REAL,
  PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS analysis_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  checkpoint TEXT NOT NULL,
  final_decision_summary TEXT,
  payload_json TEXT,
  status TEXT DEFAULT 'ok',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analysis_symbol_date_ckpt
  ON analysis_history(symbol, trade_date, checkpoint);

CREATE TABLE IF NOT EXISTS validation_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_history_id INTEGER,
  symbol TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  checkpoint TEXT,
  verdict TEXT,
  score REAL,
  details_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (analysis_history_id) REFERENCES analysis_history(id)
);

CREATE INDEX IF NOT EXISTS idx_validation_symbol_date
  ON validation_results(symbol, trade_date);

CREATE TABLE IF NOT EXISTS sync_runs (
  run_id TEXT PRIMARY KEY,
  mode TEXT NOT NULL,
  tables_json TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  total_rows INTEGER DEFAULT 0,
  error_message TEXT,
  stats_json TEXT
);

-- 筹码分布
CREATE TABLE IF NOT EXISTS cyq_chips (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  price REAL,
  percent REAL,
  PRIMARY KEY (ts_code, trade_date, price)
);
CREATE INDEX IF NOT EXISTS idx_cyq_trade_date ON cyq_chips(trade_date);

-- 集合竞价（收盘）
CREATE TABLE IF NOT EXISTS stk_auction_c (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  close REAL,
  open REAL,
  high REAL,
  low REAL,
  vol REAL,
  amount REAL,
  vwap REAL,
  PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_auction_c_trade_date ON stk_auction_c(trade_date);

-- 集合竞价（开盘）
CREATE TABLE IF NOT EXISTS stk_auction_o (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  close REAL,
  open REAL,
  high REAL,
  low REAL,
  vol REAL,
  amount REAL,
  vwap REAL,
  PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_auction_o_trade_date ON stk_auction_o(trade_date);

-- 融资融券
CREATE TABLE IF NOT EXISTS margin (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  rzye REAL,
  rqye REAL,
  rzmre REAL,
  rqyl REAL,
  rzche REAL,
  rqchl REAL,
  rqmcl REAL,
  rzrqye REAL,
  PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_margin_trade_date ON margin(trade_date);

-- 龙虎榜
CREATE TABLE IF NOT EXISTS top_list (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  name TEXT,
  close REAL,
  pct_change REAL,
  turnover_rate REAL,
  amount REAL,
  l_sell REAL,
  l_buy REAL,
  l_amount REAL,
  net_amount REAL,
  net_rate REAL,
  amount_rate REAL,
  float_values REAL,
  reason TEXT,
  PRIMARY KEY (trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_toplist_ts_code ON top_list(ts_code);

-- 大宗交易
CREATE TABLE IF NOT EXISTS block_trade (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  price REAL,
  vol REAL,
  amount REAL,
  buyer TEXT,
  seller TEXT,
  PRIMARY KEY (trade_date, ts_code, buyer, seller)
);
CREATE INDEX IF NOT EXISTS idx_blocktrade_ts_code ON block_trade(ts_code);

-- 资金流向
CREATE TABLE IF NOT EXISTS moneyflow (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  buy_sm_vol REAL,
  buy_sm_amount REAL,
  sell_sm_vol REAL,
  sell_sm_amount REAL,
  buy_md_vol REAL,
  buy_md_amount REAL,
  sell_md_vol REAL,
  sell_md_amount REAL,
  buy_lg_vol REAL,
  buy_lg_amount REAL,
  sell_lg_vol REAL,
  sell_lg_amount REAL,
  buy_elg_vol REAL,
  buy_elg_amount REAL,
  sell_elg_vol REAL,
  sell_elg_amount REAL,
  net_mf_vol REAL,
  net_mf_amount REAL,
  PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_moneyflow_trade_date ON moneyflow(trade_date);

-- 个股资金流向（同花顺）
CREATE TABLE IF NOT EXISTS moneyflow_ths (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  name TEXT,
  pct_change REAL,
  latest REAL,
  net_amount REAL,
  net_d5_amount REAL,
  buy_lg_amount REAL,
  buy_lg_amount_rate REAL,
  buy_md_amount REAL,
  buy_md_amount_rate REAL,
  buy_sm_amount REAL,
  buy_sm_amount_rate REAL,
  PRIMARY KEY (trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_moneyflow_ths_trade_date ON moneyflow_ths(trade_date);
CREATE INDEX IF NOT EXISTS idx_moneyflow_ths_ts_code ON moneyflow_ths(ts_code);

-- 分钟线
CREATE TABLE IF NOT EXISTS minute_kline (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  time TEXT NOT NULL,
  price REAL,
  volume REAL,
  amount REAL,
  PRIMARY KEY (ts_code, trade_date, time)
);
CREATE INDEX IF NOT EXISTS idx_minute_trade_date ON minute_kline(trade_date);

-- 月线
CREATE TABLE IF NOT EXISTS monthly (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  close REAL,
  open REAL,
  high REAL,
  low REAL,
  pre_close REAL,
  change REAL,
  pct_chg REAL,
  vol REAL,
  amount REAL,
  end_date TEXT,
  freq TEXT,
  PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_monthly_trade_date ON monthly(trade_date);

-- 周线
CREATE TABLE IF NOT EXISTS weekly (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  close REAL,
  open REAL,
  high REAL,
  low REAL,
  pre_close REAL,
  change REAL,
  pct_chg REAL,
  vol REAL,
  amount REAL,
  end_date TEXT,
  freq TEXT,
  PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_weekly_trade_date ON weekly(trade_date);

-- 回购
CREATE TABLE IF NOT EXISTS repurchase (
  ts_code TEXT NOT NULL,
  ann_date TEXT NOT NULL,
  end_date TEXT,
  proc TEXT,
  exp_date TEXT,
  vol REAL,
  amount REAL,
  high_limit REAL,
  low_limit REAL,
  PRIMARY KEY (ts_code, ann_date)
);
CREATE INDEX IF NOT EXISTS idx_repurchase_ts_code ON repurchase(ts_code);

-- 股押明细
CREATE TABLE IF NOT EXISTS pledge_detail (
  ts_code TEXT NOT NULL,
  ann_date TEXT NOT NULL,
  holder_name TEXT,
  pledge_amount REAL,
  start_date TEXT,
  end_date TEXT,
  is_release REAL,
  release_date TEXT,
  pledgor TEXT,
  holding_amount REAL,
  pledged_amount REAL,
  p_total_ratio REAL,
  h_total_ratio REAL,
  is_buyback REAL,
  PRIMARY KEY (ts_code, ann_date, holder_name)
);
CREATE INDEX IF NOT EXISTS idx_pledge_detail_ts_code ON pledge_detail(ts_code);

-- 融资融券明细
CREATE TABLE IF NOT EXISTS margin_detail (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  rzye REAL,
  rqye REAL,
  rzmre REAL,
  rqyl REAL,
  rzche REAL,
  rqchl REAL,
  rqmcl REAL,
  rzrqye REAL,
  PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_margin_detail_trade_date ON margin_detail(trade_date);

-- 十大股东
CREATE TABLE IF NOT EXISTS top10_holders (
  ts_code TEXT NOT NULL,
  ann_date TEXT NOT NULL,
  end_date TEXT,
  holder_name TEXT NOT NULL,
  hold_amount REAL,
  hold_ratio REAL,
  hold_float_ratio REAL,
  hold_change REAL,
  holder_type TEXT,
  PRIMARY KEY (ts_code, ann_date, holder_name)
);
CREATE INDEX IF NOT EXISTS idx_top10_holders_ts_code ON top10_holders(ts_code);

-- 十大流通股东
CREATE TABLE IF NOT EXISTS top10_floatholders (
  ts_code TEXT NOT NULL,
  ann_date TEXT NOT NULL,
  end_date TEXT,
  holder_name TEXT NOT NULL,
  hold_amount REAL,
  hold_ratio REAL,
  hold_float_ratio REAL,
  hold_change REAL,
  holder_type TEXT,
  PRIMARY KEY (ts_code, ann_date, holder_name)
);
CREATE INDEX IF NOT EXISTS idx_top10_floatholders_ts_code ON top10_floatholders(ts_code);

-- 龙虎榜机构明细
CREATE TABLE IF NOT EXISTS top_inst (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  exalter TEXT NOT NULL,
  side REAL,
  buy REAL,
  buy_rate REAL,
  sell REAL,
  sell_rate REAL,
  net_buy REAL,
  reason TEXT,
  PRIMARY KEY (trade_date, ts_code, exalter)
);
CREATE INDEX IF NOT EXISTS idx_top_inst_ts_code ON top_inst(ts_code);

-- 涨停池(THS)
CREATE TABLE IF NOT EXISTS limit_list_ths (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  name TEXT,
  price REAL,
  pct_chg REAL,
  open_num REAL,
  lu_desc TEXT,
  limit_type TEXT,
  tag TEXT,
  status TEXT,
  limit_order REAL,
  limit_amount REAL,
  turnover_rate REAL,
  free_float REAL,
  lu_limit_order REAL,
  limit_up_suc_rate REAL,
  turnover REAL,
  market_type TEXT,
  PRIMARY KEY (trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_limit_list_ths_ts_code ON limit_list_ths(ts_code);

-- 九转回数据
CREATE TABLE IF NOT EXISTS stk_nineturn (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  freq TEXT,
  open REAL,
  high REAL,
  low REAL,
  close REAL,
  vol REAL,
  amount REAL,
  up_count REAL,
  down_count REAL,
  nine_up_turn REAL,
  nine_down_turn REAL,
  PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_stk_nineturn_trade_date ON stk_nineturn(trade_date);

-- 连板数
CREATE TABLE IF NOT EXISTS limit_step (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  name TEXT,
  nums INTEGER,
  PRIMARY KEY (trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_limit_step_ts_code ON limit_step(ts_code);

-- 解禁股
CREATE TABLE IF NOT EXISTS share_float (
  ts_code TEXT NOT NULL,
  ann_date TEXT NOT NULL,
  float_date TEXT,
  float_share REAL,
  float_ratio REAL,
  holder_name TEXT,
  share_type TEXT,
  PRIMARY KEY (ts_code, ann_date, float_date, holder_name)
);
CREATE INDEX IF NOT EXISTS idx_share_float_ts_code ON share_float(ts_code);

-- 股押统计
CREATE TABLE IF NOT EXISTS pledge_stat (
  ts_code TEXT NOT NULL,
  end_date TEXT NOT NULL,
  pledge_count REAL,
  unrest_pledge REAL,
  rest_pledge REAL,
  total_share REAL,
  pledge_ratio REAL,
  PRIMARY KEY (ts_code, end_date)
);
CREATE INDEX IF NOT EXISTS idx_pledge_stat_ts_code ON pledge_stat(ts_code);

-- 股票基本信息
CREATE TABLE IF NOT EXISTS stock_basic (
  ts_code TEXT PRIMARY KEY,
  symbol TEXT,
  name TEXT,
  area TEXT,
  industry TEXT,
  cnspell TEXT,
  market TEXT,
  list_date TEXT,
  act_name TEXT,
  act_ent_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_stock_basic_industry ON stock_basic(industry);

-- 周月线合并（stk_weekly_monthly）
CREATE TABLE IF NOT EXISTS stk_weekly_monthly (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  close REAL,
  open REAL,
  high REAL,
  low REAL,
  pre_close REAL,
  change REAL,
  pct_chg REAL,
  vol REAL,
  amount REAL,
  PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_stk_weekly_monthly_trade_date ON stk_weekly_monthly(trade_date);

-- 原有索引
CREATE INDEX IF NOT EXISTS idx_daily_trade_date ON daily_ohlcv(trade_date);
CREATE INDEX IF NOT EXISTS idx_daily_basic_trade_date ON daily_basic(trade_date);
CREATE INDEX IF NOT EXISTS idx_stk_factor_trade_date ON stk_factor_pro(trade_date);

-- 筹码绩效
CREATE TABLE IF NOT EXISTS cyq_perf (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  his_low REAL,
  his_high REAL,
  cost_5pct REAL,
  cost_15pct REAL,
  cost_50pct REAL,
  cost_85pct REAL,
  cost_95pct REAL,
  weight_avg REAL,
  winner_rate REAL,
  PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_cyq_perf_trade_date ON cyq_perf(trade_date);

-- 游资列表
CREATE TABLE IF NOT EXISTS hm_list (
  name TEXT PRIMARY KEY,
  desc TEXT,
  orgs TEXT,
  update_date TEXT
);

-- 游资明细
CREATE TABLE IF NOT EXISTS hm_detail (
  trade_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  ts_name TEXT,
  buy_amount REAL,
  sell_amount REAL,
  net_amount REAL,
  hm_name TEXT NOT NULL,
  hm_orgs TEXT,
  PRIMARY KEY (trade_date, ts_code, hm_name)
);
CREATE INDEX IF NOT EXISTS idx_hm_detail_ts_code ON hm_detail(ts_code);

-- 涨停概念列表
CREATE TABLE IF NOT EXISTS limit_cpt_list (
  ts_code TEXT NOT NULL,
  name TEXT,
  trade_date TEXT NOT NULL,
  days INTEGER,
  up_stat TEXT,
  cons_nums INTEGER,
  up_nums INTEGER,
  pct_chg REAL,
  rank INTEGER,
  PRIMARY KEY (trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_limit_cpt_list_trade_date ON limit_cpt_list(trade_date);

-- 板块分析
CREATE TABLE IF NOT EXISTS sector_analysis (
  trade_date TEXT NOT NULL,
  sector TEXT,
  ts_code TEXT NOT NULL,
  name TEXT,
  market_cap REAL,
  pe REAL,
  pb REAL,
  turnover_rate REAL,
  pct_chg REAL,
  sector_strength REAL,
  PRIMARY KEY (trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_sector_analysis_trade_date ON sector_analysis(trade_date);
CREATE INDEX IF NOT EXISTS idx_sector_analysis_sector ON sector_analysis(sector);

-- 异常波动
CREATE TABLE IF NOT EXISTS stk_shock (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  name TEXT,
  trade_market TEXT,
  reason TEXT,
  period TEXT,
  PRIMARY KEY (trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_stk_shock_ts_code ON stk_shock(ts_code);

-- 开盘啦涨停列表
CREATE TABLE IF NOT EXISTS kpl_list (
  ts_code TEXT NOT NULL,
  name TEXT,
  trade_date TEXT NOT NULL,
  lu_time TEXT,
  ld_time TEXT,
  open_time TEXT,
  last_time TEXT,
  lu_desc TEXT,
  tag TEXT,
  theme TEXT,
  net_change REAL,
  bid_amount REAL,
  status TEXT,
  bid_change REAL,
  bid_turnover REAL,
  lu_bid_vol REAL,
  pct_chg REAL,
  bid_pct_chg REAL,
  rt_pct_chg REAL,
  limit_order REAL,
  amount REAL,
  turnover_rate REAL,
  free_float REAL,
  lu_limit_order REAL,
  PRIMARY KEY (trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_kpl_list_trade_date ON kpl_list(trade_date);

-- 开盘啦概念成分
CREATE TABLE IF NOT EXISTS kpl_concept_cons (
  ts_code TEXT NOT NULL,
  name TEXT,
  con_name TEXT,
  con_code TEXT,
  trade_date TEXT NOT NULL,
  desc TEXT,
  hot_num REAL,
  PRIMARY KEY (trade_date, ts_code, con_code)
);
CREATE INDEX IF NOT EXISTS idx_kpl_concept_cons_con_code ON kpl_concept_cons(con_code);

-- Datayes 概念 (统一收录 dc_concept + dc_concept_cons)
CREATE TABLE IF NOT EXISTS dc_concept (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  name TEXT,
  theme_code TEXT,
  industry_code TEXT,
  industry TEXT,
  reason TEXT,
  hot_num REAL,
  concept_name TEXT,
  concept_hot REAL,
  concept_strength REAL,
  concept_pct_change REAL,
  lead_stock TEXT,
  PRIMARY KEY (ts_code, trade_date, theme_code)
);
CREATE INDEX IF NOT EXISTS idx_dc_concept_trade_date ON dc_concept(trade_date);
CREATE INDEX IF NOT EXISTS idx_dc_concept_concept_name ON dc_concept(concept_name);

-- Datayes 概念成分 (dc_concept_cons)
CREATE TABLE IF NOT EXISTS dc_concept_cons (
  ts_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  name TEXT,
  theme_code TEXT,
  industry_code TEXT,
  industry TEXT,
  reason TEXT,
  hot_num REAL,
  PRIMARY KEY (ts_code, trade_date, theme_code)
);
CREATE INDEX IF NOT EXISTS idx_dc_concept_cons_trade_date ON dc_concept_cons(trade_date);
