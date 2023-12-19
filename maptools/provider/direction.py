#%%
import json
import requests
import numpy as np
import pandas as pd
from copy import deepcopy
from loguru import logger

ROUTE_COLUMNS = ['route', 'direct', 'seg_id', 'type', 'name', 'line_id', 'departure_stop', 'arrival_stop',  'distance', 'cost', 'walking_0_info']
KEY = "25db7e8486211a33a4fcf5a80c22eaf0"


def __filter_dataframe_columns(df, cols=ROUTE_COLUMNS):
    cols = [i for i in cols if i in list(df)]
    
    return df[cols]

def query_transit_directions(src, dst, city1, city2, key, strategy=0, show_fields='cost,navi', multiexport=1, memo={}, desc=None):
    """
    高德地图公交路线规划 API 服务地址

    strategy: 
        0: 推荐模式, 综合权重, 同高德APP默认
        1: 最经济模式, 票价最低
        2: 最少换乘模式, 换乘次数少
        3: 最少步行模式, 尽可能减少步行距离
        4: 最舒适模式, 尽可能乘坐空调车
        5: 不乘地铁模式, 不乘坐地铁路线
        6: 地铁图模式, 起终点都是地铁站（地铁图模式下 originpoi 及 destinationpoi 为必填项）
        7: 地铁优先模式, 步行距离不超过4KM
        8: 时间短模式, 方案花费总时间最少
    Ref:
        - https://lbs.amap.com/api/webservice/guide/api/newroute#t9
    """
    if (src, dst, strategy) in memo:
        return memo[(src, dst, strategy)]
    
    url = "https://restapi.amap.com/v5/direction/transit/integrated"
    params = {
        'key': key,
        'origin': src,
        'destination': dst,
        'city1': city1,
        'city2': city2,
        'strategy': strategy,
        'show_fields': show_fields,
        'multiexport': multiexport
    }
    _url = f"{url}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
    if desc:
        _url = str(desc) + ': ' + _url
    logger.debug(_url)

    response = requests.get(url, params=params)
    response = json.loads(response.text)
    memo[(src, dst, strategy)] = response

    return response

def parse_transit_directions(data, mode='地铁线路', verbose=False):
    def _extract_steps_from_plan(route, route_id):
        steps = []
        for seg_id, segment in enumerate(route['segments']):
            connector = 'walking_0'
            step = {"seg_id": seg_id, 'mode': ",".join(segment.keys())}
            for key, val in segment.items():
                val = deepcopy(val)
                if key == 'bus':
                    connector = 'walking_1'
                    if len(val['buslines']) != 1:
                         # 针对公交场景，存在2条或以上的公交车共线的情况，但就地铁而言不存在此情况
                        modes = np.unique([item['type'] for item in val['buslines']])
                        if len(modes) > 1:
                            logger.warning(f"Check route {route_id} the buslines length, types: {list(modes)}")
                    line = val['buslines'][0]
                    step.update(line)
                elif key == 'walking':
                    step[connector] = val
                    step[connector+"_info"] = {
                        "cost": int(val['cost']['duration']), 
                        "distance": int(val['distance'])
                    }
                elif key == 'taxi':
                    step.update(val)
            steps.append(step)                    

        # 删除首尾步行的部分
        steps = pd.DataFrame(steps)
        if steps.iloc[0].type != steps.iloc[0].type:
            steps = steps.iloc[1:]
        if steps.iloc[-1].type != steps.iloc[-1].type:
            steps = steps.iloc[:-1]
        
        return steps

    routes = pd.DataFrame()
    transits = data.get('route', {}).get("transits")
    
    # `transits` is None
    if not transits:
        logger.warning("No tranists records!")
        return routes
    
    lst = []
    for i, transit in enumerate(transits, start=0):
        routes = _extract_steps_from_plan(transit, i)
        routes.loc[:, 'direct'] = True if len(routes) == 1 else False
        # if mode is not None and mode not in routes['type'].unique():
        #     continue
        routes.loc[:, 'route'] = i
        lst.append(routes)
    
    if lst: routes = pd.concat(lst).reset_index(drop=True)

    routes = routes.replace('', np.nan).dropna(axis=1, how='all')
    routes.rename(columns={'id': 'line_id'}, inplace=True)
    routes.loc[:, 'cost'] = routes.cost.apply(
        lambda x: x.get('duration', np.nan) if isinstance(x, dict) else x)

    return routes
    
def extract_walking_steps_from_routes(routes:pd.DataFrame):
    def extract_walking_steps(route):
        if 'walking_0_info' not in list(route):
            return pd.DataFrame()

        walkings = []
        prev = route.iloc[0].arrival_stop
        prev_mode = route.iloc[0].type

        for seg in route.iloc[1:].itertuples():
            if prev_mode == '地铁线路':
                cur = seg.departure_stop
                info = {'src': prev, 'dst': cur}
                if seg.walking_0_info is not None and \
                    seg.walking_0_info == seg.walking_0_info:
                    info.update(seg.walking_0_info)
                walkings.append(info)
                
            prev = seg.arrival_stop
            prev_mode = seg.type

        return pd.DataFrame(walkings)

    route_ids = routes.route.unique()

    walkinhg_steps_lst = []
    for idx in route_ids:
        route = routes.query(f"route == {idx}")
        walkinhg_steps = extract_walking_steps(route)
        if walkinhg_steps.empty:
            continue
        walkinhg_steps.loc[:, 'route'] = idx
        walkinhg_steps_lst.append(walkinhg_steps)

    if len(walkinhg_steps_lst) == 0:
        return pd.DataFrame()

    walkings = pd.concat(walkinhg_steps_lst, axis=0)#.drop_duplicates(['src', 'dst'])
    walkings.loc[:, 'station_name'] = walkings.src.apply(lambda x: x['name'])
    walkings.loc[:, 'src_id'] = walkings.src.apply(lambda x: x['id'])
    walkings.loc[:, 'dst_id'] = walkings.dst.apply(lambda x: x['id'])
    walkings.loc[:, 'src_loc'] = walkings.src.apply(lambda x: x['location'])
    walkings.loc[:, 'dst_loc'] = walkings.dst.apply(lambda x: x['location'])
    walkings.loc[:, 'same_loc'] = walkings.src_loc == walkings.dst_loc
    walkings.drop(columns=['src', 'dst'], inplace=True)
    attrs = list(walkings)
    attrs.remove('cost')
    attrs.remove('distance')
    attrs += ['cost', 'distance']

    walkings.drop_duplicates(['src_id', 'dst_id', 'src_loc'], inplace=True)

    return walkings[attrs]

def filter_route_by_lineID(routes, src, dst):
    try:
        src_line_id = src.line_id
        dst_line_id = dst.line_id
        if src_line_id is None or dst_line_id is None:
            return routes
    except:
        logger.warning("(src, dst) don't have `line_id` attribute.")
        return routes
    
    route_ids = None
    waylines = routes.groupby('route').line_id.apply(list)
    cond = waylines.apply(lambda x: x[0] == src_line_id and x[-1] == dst_line_id)
    route_ids = waylines[cond].index
            
    if route_ids is not None:
        return routes.query("route in @route_ids")

    return routes

def get_routes(src:pd.Series, dst:pd.Series, strategy:int, citycode:str='0755', mode:str='地铁线路', memo:dict={}):
    
    response_data = query_transit_directions(
        src.location, dst.location, citycode, citycode, KEY, strategy, memo=memo, desc=f"{src.name} --> {dst.name}")
    routes = parse_transit_directions(response_data, mode=mode)
    routes.loc[:, 'memo'] = f"{src.name} --> {dst.name}"
    # routes = __filter_dataframe_columns(routes)
    
    routes = filter_route_by_lineID(routes, src, dst)
    walkings = extract_walking_steps_from_routes(routes)
    
    return routes, walkings


#%%
if __name__ == "__main__":
    tets_case = 1
    # 南山 --> 上梅林
    src, dst = [pd.Series({
        'id': 'BV10244676',
        'location': '113.923483,22.524037',
        'name': '南山',
        'sequence': '14',
        'line_id': '440300024057',
        'line_name': '地铁11号线',
    }), pd.Series(
        {'id': 'BV10243815',
        'location': '114.059415,22.570459',
        'name': '上梅林',
        'sequence': '7',
        'line_id': '440300024075',
        'line_name': '地铁4号线'
    })]

    # 南山 --> 福田 (line 11)
    src, dst = [pd.Series({
        'id': 'BV10244676',
        'location': '113.923483,22.524037',
        'name': '南山',
        'sequence': '14',
        'line_id': '440300024057',
        'line_name': '地铁11号线',
    }), pd.Series(
        {
        'location': '114.055636,22.539872',
        'name': '福田',
        'sequence': '17',
        'line_id': '440300024057',
        'line_name': '地铁11号线'
    })]

    # 海山 --> 小梅沙
    src, dst = [pd.Series({
        'id': 'BV10244749',
        'location': '114.237711,22.555537',
        'name': '海山',
        'sequence': '35',
        'line_id': '440300024076',
        'line_name': '地铁2号线'}),
        pd.Series(
        {'id': 'BV10804214',
        'location': '114.326201,22.601932',
        'name': '小梅沙',
        'sequence': '42',
        'line_id': '440300024076',
        'line_name': '地铁2号线'},
    )]
    
    # 西丽湖 --> 福邻
    src, dst = [
        pd.Series({'id': 'BV10602481',
        'location': '113.965648,22.593567',
        'name': '西丽湖',
        'sequence': '1',
        'line_id': '440300024050',
        'line_name': '地铁7号线'}),
        pd.Series({'id': 'BV10602480',
        'location': '114.081263,22.524656',
        'name': '福邻',
        'sequence': '17',
        'line_id': '440300024050',
        'line_name': '地铁7号线'})
    ]

    citycode = '0755'
    data = query_transit_directions(src.location, dst.location, citycode, citycode, KEY)

    # %%
    df_routes = parse_transit_directions(data)
    df_routes = __filter_dataframe_columns(df_routes)
    df_routes.loc[:, 'memo'] = f"{src['name']} --> {dst['name']}"
    df_routes

    # %%
    df_routes = filter_route_by_lineID(df_routes, src, dst)
    df_routes

    # %%
    walkings = extract_walking_steps_from_routes(df_routes)
    walkings

    # %%
    routes, walkings = get_routes(src, dst, strategy=0)
    __filter_dataframe_columns(routes)
    
    # %%
    walkings

# %%
