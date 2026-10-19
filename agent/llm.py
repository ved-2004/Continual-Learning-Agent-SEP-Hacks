import hashlib, io, json, os, re, time
from contextlib import redirect_stdout, redirect_stderr
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, TypeVar
import anthropic
import openai
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)
_forbidden, _run_dir = [], None
# None is the CLI; an empty string is a visitor who supplied no key.
visitor_key = ContextVar("visitor_key", default=None)
before_request = ContextVar("before_request", default=lambda: None)
class MissingAPIKey(RuntimeError): pass
class RejectedAPIKey(RuntimeError): pass
def set_forbidden(texts):
    global _forbidden
    _forbidden = [text for text in texts if len(text) >= 20]

def start_run(run_dir):
    global _run_dir
    _run_dir = Path(run_dir)
    _run_dir.mkdir(parents=True, exist_ok=True)
    (_run_dir / "llm_calls.jsonl").write_text("")

def request(provider, model, system, user):
    key = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    api_key = visitor_key.get() if visitor_key.get() is not None else os.environ.get(key)
    if not api_key:
        raise RuntimeError(f"Set {key} in .env before making live calls")
    kwargs = dict(model=model, temperature=0, messages=[dict(role="user", content=user)])
    if provider == "anthropic":
        client = anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=90)
        kwargs.update(system=system, max_tokens=16000, extra_body={"temperature": kwargs.pop("temperature")})
        send = client.messages.create
    else:
        client = openai.OpenAI(api_key=api_key, max_retries=0, timeout=90)
        kwargs.update(max_completion_tokens=16000, messages=[dict(role="system", content=system), *kwargs["messages"]])
        send = client.chat.completions.create
    with client:
        try:
            response = send(**kwargs)
        except (anthropic.BadRequestError, openai.BadRequestError) as exc:
            message = str(exc).lower()
            if "temperature" not in message or not any(term in message for term in ("unsupported", "not support", "not allowed", "deprecated", "only")):
                raise
            print(f"Warning: {model} rejects temperature=0; retrying without temperature.")
            kwargs.pop("temperature", None)
            kwargs.pop("extra_body", None)
            response = send(**kwargs)
    stop = response.stop_reason if provider == "anthropic" else response.choices[0].finish_reason
    if stop in ("max_tokens", "length"):
        raise RuntimeError(f"output truncated: {provider}/{model} hit the output token limit ({stop}); reply not cached or retried")
    raw = "".join(block.text for block in response.content if block.type == "text") if provider == "anthropic" else response.choices[0].message.content
    return raw if isinstance(raw, str) else json.dumps(raw)

def call(step: str, day: int, system: str, user: str, out: type[M], check: Callable[[M], list[str]]) -> M:
    original = user
    for attempt in (1, 2):
        if any(text in system or text in user for text in _forbidden):
            raise RuntimeError(f"{step} day {day}: forbidden hidden_fact in prompt")
        if _run_dir is None:
            raise RuntimeError("Call start_run before an LLM call")
        load_dotenv(Path(".env"))
        provider, model = os.environ.get("LLM_PROVIDER"), os.environ.get("LLM_MODEL")
        if provider not in ("anthropic", "openai") or not model:
            raise RuntimeError("Set LLM_PROVIDER (anthropic/openai) and LLM_MODEL in .env")
        key = hashlib.sha256(json.dumps([provider, model, system, user]).encode()).hexdigest()
        cache = Path(os.environ.get("CACHE_DIR", ".cache")) / f"{key}.txt"
        started, hit, errors = time.perf_counter(), cache.exists(), []
        try:
            if hit:
                raw = cache.read_text()
            elif visitor_key.get() is None:
                raw = request(provider, model, system, user)
            else:
                if not visitor_key.get(): raise MissingAPIKey("Visitor API key required")
                before_request.get()()
                try:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        raw = request(provider, model, system, user)
                except (anthropic.AuthenticationError, openai.AuthenticationError):
                    raise RejectedAPIKey("Provider rejected visitor API key") from None
                except Exception:
                    raise RuntimeError("Model request failed") from None
                raw = raw.replace(visitor_key.get(), "[redacted]")
            if not hit:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(raw)
            try:
                clean = re.sub(r"\A```(?:json)?\s*\n?(.*?)\n?```\Z", r"\1", raw.strip(), flags=re.S)
                result = out.model_validate(json.loads(clean))
                errors = check(result)
            except ValidationError as exc:
                errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
            except (ValueError, TypeError) as exc:
                errors = [str(exc)]
        except Exception as exc:
            errors = [str(exc)]
            raise
        finally:
            with (_run_dir / "llm_calls.jsonl").open("a") as log:
                log.write(json.dumps(dict(step=step, day=day, attempt=attempt, cache_hit=hit, seconds=round(time.perf_counter() - started, 4), errors=[e[:200] for e in errors])) + "\n")
        if not errors:
            return result
        user = original + "\n\nYour previous reply failed these checks:\n- " + "\n- ".join(errors) + "\n\nReply again with corrected JSON only."
    failed = _run_dir / f"failed_{step}_day{day:02}.txt"
    failed.write_text(raw)
    raise RuntimeError(f"{step} day {day}: {'; '.join(errors)}. Raw reply saved to {failed}")
