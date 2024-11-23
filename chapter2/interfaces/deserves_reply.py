import ontology
from declarations import GenerateResponse, ActionHistory


async def deserves_reply(
    generate_response: GenerateResponse,
    config: ontology.Config,
    message_history: ActionHistory,
    reply_on_sim: ontology.ReplyOnSimConfig,
) -> bool:
    response = generate_response(
        ontology.load_config_from_kv(
            {"em": {"vendors": config.em.vendors, **reply_on_sim.em_overrides}},
            config.model_dump(),
        ).em,
        message_history,
    )
    match reply_on_sim.match:
        case "predict_username":
            try:
                first_message = await anext(aiter(response))
            except StopAsyncIteration:
                return False
            return (
                first_message.author is not None
                and first_message.author.name == config.em.name
            )
