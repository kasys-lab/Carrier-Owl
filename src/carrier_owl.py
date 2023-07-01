from webdriver_manager.firefox import GeckoDriverManager
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.common.exceptions import NoSuchElementException
import os
import time
import yaml
import datetime
import slackweb
import argparse
import textwrap
from bs4 import BeautifulSoup
import warnings
import urllib.parse
import dataclasses
from dataclasses import dataclass
import arxiv
import requests
import random
# setting
warnings.filterwarnings('ignore')


@dataclass
class Result:
    url: str
    title: str
    abstract: str
    words: list
    score: float = 0.0


def calc_score(abst: str, keywords: dict) -> (float, list):
    sum_score = 0.0
    hit_kwd_list = []

    for word in keywords.keys():
        score = keywords[word]
        if word.lower() in abst.lower():
            sum_score += score
            hit_kwd_list.append(word)
    return sum_score, hit_kwd_list

def translate_results(results: list) -> list:
    translated_results = []
    
    # ヘッドレスモードでブラウザを起動
    options = Options()
    options.add_argument('--headless')

    # ブラウザーを起動
    driver = webdriver.Firefox(executable_path=GeckoDriverManager().install(), options=options)
    
    for result in results:
        title_trans = get_translated_text('ja', 'en', result.title, driver)
        abstract = result.abstract.replace('\n', '')
        abstract_trans = get_translated_text('ja', 'en', abstract, driver)
        translated_result = dataclasses.replace(result, title=title_trans, abstract=abstract_trans)
        translated_results.append(translated_result)
    
    # ブラウザ停止
    driver.quit()
    return translated_results


def search_keyword(
        articles: list, keywords: dict, score_threshold: float, translate: bool = True
        ) -> list:
    results = []

    for article in articles:
        url = article['arxiv_url']
        title = article['title']
        abstract = article['summary']
        if len(keywords) > 0:
            score, hit_keywords = calc_score(abstract, keywords)
        else:
            score, hit_keywords = 1, []
        if (score != 0) and (score >= score_threshold):
            result = Result(
                        url=url, title=title, abstract=abstract,
                        score=score, words=hit_keywords)
            results.append(result)
    
    results = translate_results(results) if translate else results

    return results


def send2app(text: str, slack_id: str, line_token: str) -> None:
    # slack
    if slack_id is not None:
        slack = slackweb.Slack(url=slack_id)
        slack.notify(text=text)

    # line
    if line_token is not None:
        line_notify_api = 'https://notify-api.line.me/api/notify'
        headers = {'Authorization': f'Bearer {line_token}'}
        data = {'message': f'message: {text}'}
        requests.post(line_notify_api, headers=headers, data=data)


def notify(results: list, slack_id: str, line_token: str, note: str = '') -> None:
    # 通知
    star = '*'*80
    today = datetime.date.today()
    n_articles = len(results)
    text = f'{star}\n \t \t {today}\tnum of articles = {n_articles}\n\t{note}\n\n{star}'
    send2app(text, slack_id, line_token)
    # descending
    for result in sorted(results, reverse=True, key=lambda x: x.score):
        url = result.url
        title = result.title
        abstract = result.abstract
        word = result.words
        score = result.score

        text = f'\n score: `{score}`'\
               f'\n hit keywords: `{word}`'\
               f'\n url: {url}'\
               f'\n title:    {title}'\
               f'\n abstract:'\
               f'\n \t {abstract}'\
               f'\n {star}'

        send2app(text, slack_id, line_token)


def get_translated_text(from_lang: str, to_lang: str, from_text: str, driver) -> str:
    '''
    https://qiita.com/fujino-fpu/items/e94d4ff9e7a5784b2987
    '''

    sleep_time = 1

    # urlencode
    from_text = urllib.parse.quote(from_text)

    # url作成
    url = 'https://www.deepl.com/translator#' \
        + from_lang + '/' + to_lang + '/' + from_text

    driver.get(url)
    driver.implicitly_wait(10)  # 見つからないときは、10秒まで待つ

    for i in range(30):
        # 指定時間待つ
        time.sleep(sleep_time)
        html = driver.page_source
        # to_text = get_text_from_page_source(html)
        to_text = get_text_from_driver(driver)

        if to_text:
            break
    if to_text is None:
        return urllib.parse.unquote(from_text)
    return to_text

def get_text_from_driver(driver) -> str:
    try:
        elem = driver.find_element_by_class_name('lmt__translations_as_text__text_btn')
    except NoSuchElementException as e:
        return None
    text = elem.get_attribute('innerHTML')
    return text

def get_text_from_page_source(html: str) -> str:
    soup = BeautifulSoup(html, features='lxml')
    target_elem = soup.find(class_="lmt__translations_as_text__text_btn")
    text = target_elem.text
    return text


def get_config() -> dict:
    file_abs_path = os.path.abspath(__file__)
    file_dir = os.path.dirname(file_abs_path)
    config_path = f'{file_dir}/../config.yaml'
    with open(config_path, 'r') as yml:
        config = yaml.load(yml)
    return config


def main():
    # debug用
    parser = argparse.ArgumentParser()
    parser.add_argument('--slack_id', default=None)
    parser.add_argument('--line_token', default=None)
    args = parser.parse_args()

    config = get_config()
    subject = config['subject']

    translate = config['translate']
    
    keywords = config['keywords'] if 'keywords' in config else {}
    score_threshold = float(config['score_threshold'])

    day_before_yesterday = datetime.datetime.today() - datetime.timedelta(days=1)
    day_before_yesterday_str = day_before_yesterday.strftime('%Y%m%d')
    # datetime format YYYYMMDDHHMMSS
    arxiv_query = f'({subject}) AND ' \
                  f'submittedDate:' \
                  f'[{day_before_yesterday_str}000000 TO {day_before_yesterday_str}235959]'
    articles = arxiv.query(query=arxiv_query,
                           max_results=1000,
                           sort_by='submittedDate',
                           iterative=False)
    note = ''
    if len(articles) <= 0:
        articles = arxiv.query(query=f'{subject}',
                           max_results=100,
                           sort_by='submittedDate',
                           iterative=False)
        articles = random.choices(articles, k=10)
        note = 'Randomly displays 10 of the last 100 results'
    results = search_keyword(articles, keywords, score_threshold, translate=translate)

    slack_id = os.getenv("SLACK_ID") or args.slack_id
    line_token = os.getenv("LINE_TOKEN") or args.line_token
    notify(results, slack_id, line_token, note=note)


if __name__ == "__main__":
    main()
