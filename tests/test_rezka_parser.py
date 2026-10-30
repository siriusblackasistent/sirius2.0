"""Exercise the dependency parser that failed after selecting a search result."""
import unittest

from bs4 import BeautifulSoup
from hdrezka.post._utils.parser import get_post_info


class RezkaParserTests(unittest.TestCase):
    def test_movie_info_with_nested_table_heading(self):
        # Soup Sieve 3.x raised SelectorSyntaxError on HDRezka's tr:has(td>h2).
        html = '''
        <h1 itemprop="name">Джентльмени</h1>
        <div class="b-post__rating">
          <span itemprop="average">8.5</span><span itemprop="votes">100</span>
        </div>
        <table>
          <tr><td><h2>Время</h2></td><td>113 мин.</td></tr>
          <tr><td><h2>В переводе</h2></td><td>Українська, Оригінал</td></tr>
          <tr><td><h2>Дата выхода</h2></td><td>1 січня <a>2019</a></td></tr>
        </table>
        '''
        info = get_post_info(BeautifulSoup(html, 'html.parser'), url='https://example.com/movie')
        self.assertEqual(info.title, 'Джентльмени')
        self.assertEqual(info.translators, ('Українська', 'Оригінал'))
        self.assertEqual(info.duration_m, 113)
        self.assertEqual(info.release.year, 2019)
