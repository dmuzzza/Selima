import pandas as pd
import numpy as np
import pandas_ta as ta
from get_data import get_candles_df
from itertools import chain


def engulfing(df):
    df['max_price_candle'] = df[['open', 'close']].max(axis=1)
    df['min_price_candle'] = df[['open', 'close']].min(axis=1)
    last_cndl_size = 0.002
    # медвежье поглощение
    
    df['bear_engulfing'] = (
        (df['candle_type'].shift(1) == 'bull') & (df['candle_type'] == 'bear') & # 1 сравнение типов свечей 
        (df['close'].shift(1) < df['open']) & (df['open'].shift(1) > df['close'])  # 2 предыдущая свеча меньше текущей
        #(abs((df['max_price_candle'].shift(1) - df['min_price_candle'].shift(1)) / df['min_price_candle'].shift(1)) >= last_cndl_size)
    )
    # бычье поглощение
    df['bull_engulfing'] = (
        (df['candle_type'].shift(1) == 'bear') & (df['candle_type'] == 'bull') & # 1 сравнение типов свечей 
        (df['close'].shift(1) > df['open']) & (df['open'].shift(1) < df['close']) # 2 предыдущая свеча меньше текущей
        #(abs((df['max_price_candle'].shift(1) - df['min_price_candle'].shift(1)) / df['min_price_candle'].shift(1)) >= last_cndl_size)
    )
    def sgnl_type(row):
        if row['bull_engulfing']:
            return 'Бычье поглощение'
        elif row['bear_engulfing']:
            return 'Медвежье поглощение'
        else:
            return None
    def sgnl_color(row):
        if row['bull_engulfing']:
            return 'green'
        elif row['bear_engulfing']:
            return 'red'
        else:
            return None
    df['signal_type'] = df.apply(sgnl_type, axis=1)
    df['signal_color'] = df.apply(sgnl_color, axis=1)
    def shadow_size(row):
        if row['candle_type'] == 'bear':
            top_shadow = round(abs((row['high'] - row['open']) / row['open']),2)
            bottom_shadow = round(abs((row['low'] - row['close']) / row['close']),2)
            return (top_shadow, bottom_shadow)
        elif row['candle_type'] == 'bull':
            top_shadow = round(abs((row['high'] - row['close']) / row['close']),2)
            bottom_shadow = round(abs((row['low'] - row['open']) / row['open']),2)
            return (top_shadow, bottom_shadow)
        else:
            return (0.0, 0.0)    
    

    def find_average_shadow(row, range_shadows = 60):
        current_idx = row.name
    
        if row['bear_engulfing'] or row['bull_engulfing']:
            if current_idx >= range_shadows:
                # Есть достаточно данных для окна в 30 свечей
                start_idx = current_idx - range_shadows
                window_data = df['shadow_t_b'].iloc[start_idx:current_idx]
            else:
                # Данных меньше 30 свечей - берем все доступные с начала
                window_data = df['shadow_t_b'].iloc[0:current_idx]
            
            # Распаковываем кортежи и вычисляем медиану
            all_values = list(chain.from_iterable(window_data.dropna()))
            
            if len(all_values) > 0:
                return np.average(all_values)
            else:
                return None
        else:
            return None
    
    
    def find_avg_candle_size(row, range_candles = 30):
        current_idx = row.name
        if row['bear_engulfing'] or row['bull_engulfing']:
            if current_idx >= range_candles:
                start_idx = current_idx - range_candles
                avg_candle_size = df['candle_size_perc'].iloc[start_idx:current_idx].mean()
                
                return avg_candle_size
            else:
                return None
        else:
            return None
        
    df['shadow_t_b'] = df.apply(shadow_size, axis=1)        
    df['avg_shadows'] = df.apply(find_average_shadow, axis=1)
    df['avg_candles'] = df.apply(find_avg_candle_size, axis=1)

    return df

def find_min_max_last_price(df, last_range_cndls = 5):
    
    def find_price(row):
        if row['bear_engulfing'] == True:
            current_index = row.name
            if current_index >= last_range_cndls:
                start_idx = current_index - last_range_cndls + 1
                min_price = df['min_price_candle'].iloc[start_idx:current_index].min()
                return min_price
            else:
                return None
        elif row['bull_engulfing'] == True:
            current_index = row.name
            if current_index >= last_range_cndls:
                start_idx = current_index - last_range_cndls + 1
                max_price = df['max_price_candle'].iloc[start_idx:current_index].max()
                return max_price
            else:
                return None
        return None  # важно возвращать значение для всех случаев
    df['min_max_price'] = df.apply(find_price, axis=1)

    # Выявление разворотных паттернов
    def find_reversal_pattern(row):
        current_index = row.name # текущая свеча
        last_index = row.name - 1 # предыдущая свеча
        if row['bear_engulfing'] == True:
            max_price_current = df['max_price_candle'].iloc[last_index]
            last_price_current = df['min_max_price'].iloc[current_index]
            price_change = (max_price_current - last_price_current) / last_price_current
            if price_change >= 0.02:
                return True
            else:
                return False
        elif row['bull_engulfing'] == True:
            min_price_current = df['min_price_candle'].iloc[last_index]
            last_price_current = df['min_max_price'].iloc[current_index]
            price_change = (min_price_current - last_price_current) / last_price_current
            if price_change <= -0.02:
                return True
            else:
                return False
        else:
            return False
    df['reversal_patt'] = df.apply(find_reversal_pattern, axis=1)
    
    # Поиск количества предыдущих свечей по типу
    def check_last_candles(row, last_range_cndls = 5): 
        current_index = row.name # текущая свеча
        
        start_index = current_index - last_range_cndls# предыдущая свеча
        if row['bear_engulfing'] == True or row['bull_engulfing'] == True:
            if current_index >= last_range_cndls:
                range_df = df.iloc[start_index:current_index]
                bull_count = (range_df['candle_type'] == 'bull').sum()
                bear_count = (range_df['candle_type'] == 'bear').sum()
                return bull_count, bear_count
            else:
                return None
        else:
            return None
    df['bull_bear_cdls_num'] = df.apply(check_last_candles, axis=1)


    return df

def check_volume(df, range_avg_volume=40):
    def find_avg_volume(row):
        current_idx = row.name
        current_pos = df.index.get_loc(current_idx)
        
        if current_pos >= range_avg_volume:
            start_pos = current_pos - range_avg_volume
            if row['bear_engulfing'] or row['bull_engulfing']:
                average_volume = df['volume'].iloc[start_pos:current_pos].mean()
                median_volume = df['volume'].iloc[start_pos:current_pos].median()
                return (average_volume, median_volume)
        return (None, None)  # Возвращаем кортеж из двух значений
    
    # Применяем функцию и разделяем результат
    result = df.apply(find_avg_volume, axis=1)
    df['avg_volume'] = result.apply(lambda x: x[0] if x else None)
    df['mdn_volume'] = result.apply(lambda x: x[1] if x else None)
    
    return df
    
def additionall_inducs(df, ma_20 = 20, ma_50 = 50, ma_100 = 100, rsi = 14):
    df['ma_20'] = ta.sma(df['close'], length=ma_20).round(2)
    df['ma_50'] = ta.sma(df['close'], length=ma_50).round(2)
    df['ma_100'] = ta.sma(df['close'], length=ma_100).round(2)
    df['rsi_14'] = ta.rsi(df['close'], length=rsi).round(2)
    return df



def result(ticker_name):
    df = get_candles_df(ticker_name)
    df = engulfing(df)
    
    df = find_min_max_last_price(df)
    df = check_volume(df)
    df = additionall_inducs(df)
    df['time'] = df['time'].dt.tz_localize(None)
    # df.to_excel('data.xlsx')
    return df

# df = result('SIBN')
# print(df[['time', 'bear_engulfing', 'bull_engulfing', 'min_price_candle', 'min_max_price', 'reversal_patt']].loc[(df['bear_engulfing'] == True) | (df['bull_engulfing'] == True)])