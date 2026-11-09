import os
from contextlib import contextmanager
from sqlalchemy import create_engine, String, Float, ForeignKey, JSON, Integer, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    at: Mapped[float] = mapped_column(Float, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class Basket(Base):
    __tablename__ = "baskets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("records.id"), unique=True)
    at: Mapped[float] = mapped_column(Float, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    basket_id: Mapped[str] = mapped_column(ForeignKey("baskets.id"), index=True)
    at: Mapped[float] = mapped_column(Float)
    data: Mapped[dict] = mapped_column(JSON)


class State(Base):
    __tablename__ = "state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


def make_engine(url=None):
    url = url or os.getenv("DATABASE_URL", "sqlite:///./terminal.db")
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def configure(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")

    return engine


@contextmanager
def transaction(engine):
    with Session(engine) as session:
        if engine.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        else:
            session.execute(text("SELECT pg_advisory_xact_lock(741228)"))
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
