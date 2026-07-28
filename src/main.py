from .commodity.commodity_risk_scorer import run_commodity_risk_scoring
from .feature_engineering import run_feature_engineering
from .modeling.prediction import run_prediction
from .modeling.training import run_training
from .news.news_risk_scorer import run_news_risk_scoring
from .preprocessing import run_preprocessing


def run_batch_pipeline() -> None:
    run_preprocessing()
    run_news_risk_scoring()
    run_commodity_risk_scoring()
    run_feature_engineering()
    run_training()
    run_prediction()


if __name__ == "__main__":
    run_batch_pipeline()
