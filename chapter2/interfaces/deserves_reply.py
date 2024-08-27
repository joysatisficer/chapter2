import ontology
from declarations import GenerateResponse, UserID, ActionHistory


async def deserves_reply(
    generate_response: GenerateResponse,
    config: ontology.Config,
    user_id: UserID,
    message_history: ActionHistory,
    reply_on_sim: ontology.ReplyOnSimConfig,
) -> bool:
    response = generate_response(
        user_id,
        message_history,
        ontology.load_config_from_kv(
            {"em": {"vendors": config.em.vendors, **reply_on_sim.em_overrides}},
            config.model_dump(),
        ).em,
    )
    match reply_on_sim.match:
        case "predict_username":
            try:
                first_message = await anext(aiter(response))
            except StopAsyncIteration:
                return False
            return (
                first_message.author is not None
                and first_message.author.user_id == user_id
            )
