# read_file 可用性测试
# 目的：验证聚宽回测环境能否读取研究平台的预测值表

PREDICTION_PATH = "/predictions/ep/_test_small.csv"


def initialize(context):
    set_benchmark("000985.XSHG")
    log.info("=== read_file 测试开始 ===")
    try:
        content = read_file(PREDICTION_PATH)
        if content is None:
            log.error("read_file 返回 None")
            return
        log.info("read_file 成功，内容类型: %s", type(content).__name__)
        if isinstance(content, str):
            lines = content.split("\n")
            log.info("文件行数（含表头）: %d", len(lines))
            log.info("前 3 行:")
            for line in lines[:3]:
                log.info("  %s", line)
        elif isinstance(content, bytes):
            log.info("文件字节数: %d", len(content))
            text = content.decode("utf-8")
            lines = text.split("\n")
            log.info("文件行数（含表头）: %d", len(lines))
            log.info("前 3 行:")
            for line in lines[:3]:
                log.info("  %s", line)
        else:
            log.info("内容: %s", str(content)[:200])
    except Exception as exc:
        log.error("read_file 失败: %s", exc)
    log.info("=== read_file 测试结束 ===")


def handle_data(context, data):
    pass
