# -*- coding: utf-8 -*-

import logging
from datetime import datetime, timedelta
from . import rules
from collections import defaultdict

logger = logging.getLogger('__main__')

DEFAULT_RULE = [
    ['keyword_in_title', 0],
    ['extract_episode'],
    ['upload_within', 365],
]

DEFAULT_ROUTER = {}

EXPIRE_DURATION = {
    'anime': 45,
    'drama': 45,
    'variety': 45,
}

def timeline(feeds, mongo_series):
    ''' 整体分析某个keyword下的数据并按时间线组织
    保存数据到collection => mongo_series
    '''
    
    # 先循环有episode的feed, 再循环没有episode的feed
    feeds_episode = []
    feeds_no_episode = []
    for feed in feeds:
        if 'episode' in feed:
            feeds_episode.append(feed)
        else:
            feeds_no_episode.append(feed)
    for feed in feeds_episode:
        # 有episode
        ep_range = list(range(int(feed['episode'][0]), int(feed['episode'][-1])+1))
        # 单剧集资源
        if len(ep_range) == 1:
            ep = int(feed['episode'][0])
            # 既有episode又有date_episode,暂时只考虑有一个date_episode
            if 'date_episode' in feed:
                date_ep = feed['date_episode'][-1]
                
                mongo_series.find_one_and_update({
                    '$or':[
                        {
                            'keyword_id': feed['keyword_id'],
                            'episode': ep,
                            'season': feed['season'][0],
                        },
                        {
                            'keyword_id': feed['keyword_id'],
                            'date_episode': date_ep,
                            'season': feed['season'][0],
                        },
                    ]
                },{
                    '$set': {
                        'keyword_id': feed['keyword_id'],
                        'episode': ep,
                        'date_episode': date_ep,
                        'keyword': feed['keyword_title'],
                        'season': feed['season'][0],
                    },
                    '$addToSet': {
                        'feeds': feed['_id'],
                        'feeds_upload_time': feed['upload_time'],
                    }
                }, upsert=True)
            else:
                mongo_series.find_one_and_update({
                    'keyword_id': feed['keyword_id'],
                    'episode': ep,
                    'season': feed['season'][0],
                },{
                    '$set': {
                        'keyword_id': feed['keyword_id'],
                        'episode': ep,
                        'keyword': feed['keyword_title'],
                        'season': feed['season'][0],
                    },
                    '$addToSet': {
                        'feeds': feed['_id'],
                        'feeds_upload_time': feed['upload_time'],
                    }
                }, upsert=True)
        # 多剧集资源
        elif len(ep_range) > 1:
            # 对于B站视频检查分P - 剧集数字必须出现在分P标题
            if feed['source'] == 'bilibili' and 'pvideo' in feed:
                pvideo_text = ' '.join(feed['pvideo'])
                for ep in ep_range:
                    if str(ep) in pvideo_text:
                        pvideo_text.replace(str(ep), '')
                        mongo_series.find_one_and_update({
                            'keyword_id': feed['keyword_id'],
                            'episode': ep,
                            'season': feed['season'][0],
                        },{
                            '$set': {
                                'keyword_id': feed['keyword_id'],
                                'episode': ep,
                                'keyword': feed['keyword_title'],
                                'season': feed['season'][0],
                            },
                            '$addToSet': {
                                'feeds': feed['_id'],
                                'feeds_upload_time': feed['upload_time'],
                            }
                        }, upsert=True)
            # 默认支持剧集数少于5，否则只取最后一个剧集
            else:
                if len(ep_range) > 5:
                    ep_range = [ep_range[-1]]
                for ep in ep_range:
                    mongo_series.find_one_and_update({
                        'keyword_id': feed['keyword_id'],
                        'episode': ep,
                        'season': feed['season'][0],
                    },{
                        '$set': {
                            'keyword_id': feed['keyword_id'],
                            'episode': ep,
                            'keyword': feed['keyword_title'],
                            'season': feed['season'][0],
                        },
                        '$addToSet': {
                            'feeds': feed['_id'],
                            'feeds_upload_time': feed['upload_time'],
                        }
                    }, upsert=True)
                    
    # 仅有date_episode, TODO - 暂只考虑一个date_episode的情况
    for feed in feeds_no_episode:
        date_ep = feed['date_episode'][-1]
        mongo_series.find_one_and_update({
            'keyword_id': feed['keyword_id'],
            'date_episode': date_ep,
            'season': feed['season'][0],
        },{
            '$set': {
                'keyword_id': feed['keyword_id'],
                'date_episode': date_ep,
                'keyword': feed['keyword_title'],
                'season': feed['season'][0],
            },
            '$addToSet': {
                'feeds': feed['_id'],
                'feeds_upload_time': feed['upload_time'],
            }
        }, upsert=True)

def analyze(feed, condition, router=DEFAULT_ROUTER):
    ''' 逐条分析采集数据，进行初步验证并解析剧集数
    '''
    feed['break_rules'] = []
    rule_list = DEFAULT_RULE
    if feed['source'] in router and feed['type'] in router:
        rule_list = router[feed['source']][feed['type']]
    for rule_obj in rule_list:
        rule_name = rule_obj[0]
        rule_args = rule_obj[1:]
        logger.debug('执行规则 %s' % rule_name)
        rule = getattr(rules, rule_name)
        breach, feed = rule.tweak(feed, condition, *rule_args)
        if not breach:
            feed['break_rules'].append(rule_name)
            logger.debug('规则 %s 验证失败！' % rule_name)
        else:
            logger.debug('规则 %s 验证成功！' % rule_name)
    if not feed['break_rules']:
        feed.pop('break_rules')
    feed['analyzed'] = True
    return feed
    
def expire(mongo_feeds, mongo_keywords):
    active_keywords = list(mongo_keywords.find({
        'status': 'activated',
    }))
    last_uploads = list(mongo_feeds.aggregate([
        {
            '$match': {
                'keyword_id': {'$in': [k['_id'] for k in active_keywords]},
                'break_rules': {'$exists': False}
            }
        },
        {
            '$group': {
                '_id': '$keyword_id',
                'last_upload': {'$max': '$upload_time'},
            }
        },
    ]))
    
    last_upload_dict = {item['_id']:item['last_upload'] for item in last_uploads}
    
    now_time = datetime.now()
    new_expire_count = 0
    for keyword in active_keywords:
        expire_dur = EXPIRE_DURATION[keyword['type']]
        if keyword['_id'] in last_upload_dict:
            last_upload = last_upload_dict[keyword['_id']]
        else:
            continue
        if now_time - last_upload > timedelta(days=expire_dur):
            logger.info('关键字%s已过期，最后更新于: %s...' % (keyword['keyword'], last_upload))
            mongo_keywords.find_one_and_update(
                {'_id': keyword['_id']},
                {
                    '$set': {'status': 'expired'}
                }
            )
            new_expire_count += 1
    return new_expire_count