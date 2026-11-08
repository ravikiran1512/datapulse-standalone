"""Safe Boolean search expression compiler with Postgres bind parameters.

Supports quoted phrases, AND, OR, NOT and parentheses; never interpolates user input as SQL.
"""
from __future__ import annotations
import re

class SearchSyntaxError(ValueError):
    pass

TOKEN = re.compile(r'"[^"\n]{1,180}"|\(|\)|\b(?:AND|OR|NOT)\b|[^\s()]+',re.I)
SEARCH_TEXT = "lower(concat_ws(' ',job_title,company_name,location,coalesce(description,''),array_to_string(tech_stack,' ')))"

class BoolParser:
    def __init__(self, query: str, params: list):
        self.tokens=TOKEN.findall(query)
        if len(self.tokens)>32 or len(query)>500:
            raise SearchSyntaxError('Search is too long')
        self.pos=0
        self.params=params

    def peek(self):
        return self.tokens[self.pos] if self.pos<len(self.tokens) else None

    def take(self):
        token=self.peek()
        if token is not None:self.pos+=1
        return token

    def term(self):
        tok=self.peek()
        if tok is None:raise SearchSyntaxError('Unexpected end of expression')
        if tok=='(':
            self.take()
            expression=self.or_expr()
            if self.take()!=')':raise SearchSyntaxError('Missing closing parenthesis')
            return f'({expression})'
        if tok.upper() in ('AND','OR',')'):
            raise SearchSyntaxError(f'Unexpected search operator: {tok}')
        self.take()
        tok=tok.strip('"')
        if not tok or tok.upper()=='NOT':raise SearchSyntaxError('Empty search term')
        self.params.append(f'%{tok.lower()}%')
        return f'{SEARCH_TEXT} LIKE ${len(self.params)}'

    def not_expr(self):
        if (self.peek() or '').upper()=='NOT':
            self.take();return f'(NOT {self.not_expr()})'
        return self.term()

    def and_expr(self):
        result=self.not_expr()
        while self.peek() not in (None,')') and (self.peek() or '').upper()!='OR':
            if (self.peek() or '').upper()=='AND':self.take()
            result=f'({result} AND {self.not_expr()})'
        return result

    def or_expr(self):
        result=self.and_expr()
        while (self.peek() or '').upper()=='OR':
            self.take();result=f'({result} OR {self.and_expr()})'
        return result

    def compile(self):
        if not self.tokens:return 'TRUE'
        result=self.or_expr()
        if self.peek() is not None:raise SearchSyntaxError('Unexpected trailing text')
        return result


def boolean_condition(query: str | None, params: list) -> str:
    return BoolParser(query or '',params).compile()
