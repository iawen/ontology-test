# -*- coding:utf-8 -*-


import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

from configs.global_config import Cfg


class Logger(object):
        # 日志
    __formatter = logging.Formatter(Cfg.log_format)
    logging.basicConfig(format=__formatter._fmt)

    logger = logging.getLogger(f"{Cfg.project_name} {Cfg.log_name}")
    logger.propagate = False

    # 控制台输出 - 只显示 WARNING 及以上（过滤掉规则详情）
    __stream_handler = logging.StreamHandler()
    __stream_handler.setFormatter(__formatter)
    __stream_handler.setLevel(logging.WARNING)
    logger.addHandler(__stream_handler)

    # 文件输出（按天轮转，保留30天）- 显示所有日志
    log_dir = "./logs"
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, Cfg.log_name + ".log")
    __file_handler = TimedRotatingFileHandler(
        log_file,
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8'
    )
    __file_handler.setFormatter(__formatter)
    __file_handler.setLevel(logging.INFO)
    __file_handler.suffix = "%Y-%m-%d"
    logger.addHandler(__file_handler)

    logger.setLevel(Cfg.log_level)

    # 进度日志 - 同时输出到控制台和文件
    def progress(self, msg, *args, **kwargs):
        """进度日志：同时输出到控制台和文件"""
        import datetime
        formatted_msg = self.replace_blank(msg, *args, **kwargs)
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # 输出到控制台（带颜色）
        print(f"{timestamp} {self.logger.name} INFO {formatted_msg}")
        # 同时写入文件
        self.logger.info(formatted_msg)

    def __init__(self, cls):
        self._cls = cls

    def __call__(self, *args, **kwargs):
        if not hasattr(self._cls, 'logger'):
            self._cls.logger = logger
        return self._cls(*args, **kwargs)

    @staticmethod
    def replace_blank(msg, *args, **kwargs):
        msg = str(msg)
        if msg.find("%") != -1:
            return msg
        return f"{msg % args}".replace("\r", " ").replace("\n", " ")

    def debug(self, msg, *args, **kwargs):
        self.logger.debug(self.replace_blank(msg, *args, **kwargs))

    def info(self, msg, *args, **kwargs):
        self.logger.info(self.replace_blank(msg, *args, **kwargs))

    def warning(self, msg, *args, **kwargs):
        self.logger.warning(self.replace_blank(msg, *args, **kwargs))

    def warn(self, msg, *args, **kwargs):
        self.logger.warn(self.replace_blank(msg, *args, **kwargs))

    def error(self, msg, *args, **kwargs):
        self.logger.error(self.replace_blank(msg, *args, **kwargs))

    def exception(self, msg, *args, **kwargs):
        self.logger.exception(self.replace_blank(msg, *args, **kwargs))

    def critical(self, msg, *args, **kwargs):
        self.logger.critical(self.replace_blank(msg, *args, **kwargs))


logger = Logger(Logger)
