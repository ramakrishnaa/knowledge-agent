"""Tool registry.

Each tool is a Pydantic input model plus a plain function. The model drives both
the JSON schema sent to the model and validation of the input the model sends back.
The knowledge-base tools themselves are defined in rag.py.
"""

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[BaseModel], str]

    def definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }

    def run(self, raw_input: object) -> str:
        """Validate the model-supplied input and run the tool. Raises on bad input."""
        return self.fn(self.input_model.model_validate(raw_input))


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, input_model: type[BaseModel]):
    def register(fn: Callable[[BaseModel], str]):
        TOOLS[name] = Tool(name, description, input_model, fn)
        return fn

    return register
