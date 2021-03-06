# -*- coding: utf-8 -*-

import logging
import requests
import six
import random

from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_core.dispatch_components import (
    AbstractRequestHandler, AbstractExceptionHandler,
    AbstractResponseInterceptor, AbstractRequestInterceptor)
from ask_sdk_core.utils import is_intent_name, is_request_type

from typing import Union, Dict, Any, List
from ask_sdk_model.dialog import (
    ElicitSlotDirective, DelegateDirective)
from ask_sdk_model import (
    Response, IntentRequest, DialogState, SlotConfirmationStatus, Slot)
from ask_sdk_model.slu.entityresolution import StatusCode

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

initial_prompt = 'This is find my best dog. Are you ready to find the best dog for you?'
default_reprompt = 'Are you ready to find the best dog for you?'


# Request Handler classes
class LaunchRequestHandler(AbstractRequestHandler):
    """Handler for skill launch."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In LaunchRequestHandler")
        speech = initial_prompt
        reprompt = default_reprompt
        handler_input.response_builder.speak(speech).ask(reprompt)
        return handler_input.response_builder.response


class InProgressPetMatchIntent(AbstractRequestHandler):
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (is_intent_name("PetMatchIntent")(handler_input)
                and handler_input.request_envelope.request.dialog_state != DialogState.COMPLETED)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In InProgressPetMatchIntent")
        current_intent = handler_input.request_envelope.request.intent
        prompt = ""

        for slot_name, current_slot in current_intent.slots.items():
            if (current_slot.confirmation_status != SlotConfirmationStatus.CONFIRMED
                    and current_slot.resolutions
                    and current_slot.resolutions.resolutions_per_authority[0]):
                if current_slot.resolutions.resolutions_per_authority[0].status.code == StatusCode.ER_SUCCESS_MATCH:
                    if len(current_slot.resolutions.resolutions_per_authority[0].values) > 1:
                        prompt = "Which would you like "

                        values = " or ".join([e.value.name for e in current_slot.resolutions.resolutions_per_authority[0].values])
                        prompt += values + " ?"
                        return handler_input.response_builder.speak(
                            prompt).ask(prompt).add_directive(
                            ElicitSlotDirective(slot_to_elicit=current_slot.name)
                        ).response
                elif current_slot.resolutions.resolutions_per_authority[0].status.code == StatusCode.ER_SUCCESS_NO_MATCH:
                    if current_slot.name in required_slots:
                        prompt = "Do you want {} to be low, medium, or high?".format(current_slot.name)

                        return handler_input.response_builder.speak(
                            prompt).ask(prompt).add_directive(
                                ElicitSlotDirective(
                                    slot_to_elicit=current_slot.name
                                )).response

        return handler_input.response_builder.add_directive(
            DelegateDirective(
                updated_intent=current_intent
            )).response


class CompletedPetMatchIntent(AbstractRequestHandler):
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (is_intent_name("PetMatchIntent")(handler_input)
                and handler_input.request_envelope.request.dialog_state == DialogState.COMPLETED)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In CompletedPetMatchIntent")
        filled_slots = handler_input.request_envelope.request.intent.slots
        slot_values = get_slot_values(filled_slots)
        pet_match_options = build_pet_match_options(slot_values=slot_values)

        try:
            response = http_get(pet_match_options)

            if response['breed'] and response['breed'] != 'None':
                speech = """A {weight}, {energy_level} energy dog that {playfulness} playful and is {affection} 
                affectionate and {training} to train is the {breed}. If you would like to learn more about {breed}, 
                say personality, description, or history.""".format(
                    energy_level=slot_values["energy"]["resolved"],
                    playfulness=slot_values['playfulness']['resolved'],
                    affection=slot_values['affection']['resolved'],
                    training=slot_values['training']['resolved'],
                    weight=slot_values['weight']['resolved'],
                    breed=response["breed"]
                )

                # set session info for persistence
                session_info = {
                    'breed': response['breed'],
                    'description': response.get('description', ''),
                    'personality': response.get('personality', ''),
                    'history': response.get('history')
                }
                handler_input.attributes_manager.session_attributes = session_info

            else:
                speech = """I am sorry I could not find a match for a {} {} energy, {} playful, {} easy to train
                and {} affectionate dog. But you can cuddle with me. Do you want to try again?""".format(
                    slot_values['weight']['resolved'],
                    slot_values["energy"]["resolved"],
                    slot_values["playfulness"]["resolved"],
                    slot_values['training']['resolved'],
                    slot_values['affection']['resolved']
                )

        except Exception as e:
            speech = "I am really sorry. I am unable to access part of my memory. Please try again later"
            logger.info("Intent: {}: message: {}".format(
                handler_input.request_envelope.request.intent.name, str(e)))

        handler_input.response_builder.speak(speech).ask(speech)
        return handler_input.response_builder.response


class DogInfoIntentHandler(AbstractRequestHandler):
    """Handler Dog Info Intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("DogInfoIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        session_info = handler_input.attributes_manager.session_attributes

        reprompt = "If you want more information about {breed}, say personality, description, or history.".format(
            breed=session_info['breed'])

        slot_values = handler_input.request_envelope.request.intent.slots

        if slot_values.get('personality').value:
            speech_text = "{personality} To learn more say description or history".format(
                personality=session_info['personality'])
        elif slot_values.get('description').value:
            speech_text = "{description} To learn more say history or personality".format(
                description=session_info['description'])
        elif slot_values.get('history').value:
            speech_text = "{history} To learn more say personality or description".format(
                history=session_info['history'])
        else:
            speech_text = reprompt

        handler_input.attributes_manager.session_attributes = session_info
        handler_input.response_builder.speak(speech_text).ask(reprompt)
        return handler_input.response_builder.response


class FallbackIntentHandler(AbstractRequestHandler):
    """Handler for handling fallback intent.

     2018-May-01: AMAZON.FallackIntent is only currently available in
     en-US locale. This handler will not be triggered except in that
     locale, so it can be safely deployed for any locale."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In FallbackIntentHandler")
        speech = "I'm sorry I can't help you with that. {}".format(initial_prompt)
        reprompt = default_reprompt
        handler_input.response_builder.speak(speech).ask(reprompt)
        return handler_input.response_builder.response


class HelpIntentHandler(AbstractRequestHandler):
    """Handler for help intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In HelpIntentHandler")
        speech = initial_prompt
        reprompt = default_reprompt

        handler_input.response_builder.speak(speech).ask(reprompt)
        return handler_input.response_builder.response


class ExitIntentHandler(AbstractRequestHandler):
    """Single Handler for Cancel, Stop and Pause intents."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (is_intent_name("AMAZON.CancelIntent")(handler_input) or
                is_intent_name("AMAZON.StopIntent")(handler_input))

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In ExitIntentHandler")
        goodbye_speech = """Remember this is just for fun. You are not getting a dog. 
        Robot voiced personal assistants are truly the best pets. You don't need a stupid app to know that!!!"""
        handler_input.response_builder.speak(goodbye_speech).set_should_end_session(
            True)
        return handler_input.response_builder.response


class SessionEndedRequestHandler(AbstractRequestHandler):
    """Handler for skill session end."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In SessionEndedRequestHandler")
        logger.info("Session ended with reason: {}".format(
            handler_input.request_envelope.request.reason))
        return handler_input.response_builder.response


# Exception Handler classes
class CatchAllExceptionHandler(AbstractExceptionHandler):
    """Catch All Exception handler.

    This handler catches all kinds of exceptions and prints
    the stack trace on AWS Cloudwatch with the request envelope."""
    def can_handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> bool
        return True

    def handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> Response
        logger.error(exception, exc_info=True)

        speech = "Sorry, I can't understand the command. Please say again."
        handler_input.response_builder.speak(speech).ask(speech)
        return handler_input.response_builder.response


# Request and Response Loggers
class RequestLogger(AbstractRequestInterceptor):
    """Log the request envelope."""
    def process(self, handler_input):
        # type: (HandlerInput) -> None
        logger.info("Request Envelope: {}".format(
            handler_input.request_envelope))


class ResponseLogger(AbstractResponseInterceptor):
    """Log the response envelope."""
    def process(self, handler_input, response):
        # type: (HandlerInput, Response) -> None
        logger.info("Response: {}".format(response))


# Data
required_slots = ["energy", "playfulness", "affection", 'training', 'weight']


# Utility functions
def get_resolved_value(request, slot_name):
    """Resolve the slot name from the request using resolutions."""
    # type: (IntentRequest, str) -> Union[str, None]
    try:
        return (request.intent.slots[slot_name].resolutions.
                resolutions_per_authority[0].values[0].value.name)
    except (AttributeError, ValueError, KeyError, IndexError, TypeError) as e:
        logger.info("Couldn't resolve {} for request: {}".format(slot_name, request))
        logger.info(str(e))
        return None


def get_slot_values(filled_slots):
    """Return slot values with additional info."""
    # type: (Dict[str, Slot]) -> Dict[str, Any]
    slot_values = {}
    logger.info("Filled slots: {}".format(filled_slots))

    for key, slot_item in six.iteritems(filled_slots):
        name = slot_item.name
        try:
            status_code = slot_item.resolutions.resolutions_per_authority[0].status.code

            if status_code == StatusCode.ER_SUCCESS_MATCH:
                slot_values[name] = {
                    "synonym": slot_item.value,
                    "resolved": slot_item.resolutions.resolutions_per_authority[0].values[0].value.name,
                    "is_validated": True,
                }
            elif status_code == StatusCode.ER_SUCCESS_NO_MATCH:
                slot_values[name] = {
                    "synonym": slot_item.value,
                    "resolved": slot_item.value,
                    "is_validated": False,
                }
            else:
                pass
        except (AttributeError, ValueError, KeyError, IndexError, TypeError) as e:
            logger.info("Couldn't resolve status_code for slot item: {}".format(slot_item))
            logger.info(e)
            slot_values[name] = {
                "synonym": slot_item.value,
                "resolved": slot_item.value,
                "is_validated": False,
            }
    return slot_values


def random_phrase(str_list):
    """Return random element from list."""
    # type: List[str] -> str
    return random.choice(str_list)


def build_pet_match_options(slot_values):
    """Return options for HTTP Get call."""

    return {
        'energy_level': slot_values['energy']['resolved'],
        'playfulness': slot_values['playfulness']['resolved'],
        'affection': slot_values['affection']['resolved'],
        'training': slot_values['training']['resolved'],
        'weight': slot_values['weight']['resolved']
    }


def http_get(params):
    base_url = 'https://68hccqr7x6.execute-api.us-east-1.amazonaws.com/dev/dog/alexa'
    response = requests.get(url=base_url, params=params)

    if response.status_code < 200 or response.status_code >= 300:
        response.raise_for_status()

    return response.json()


# Skill Builder object
sb = SkillBuilder()

# Add all request handlers to the skill.
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(InProgressPetMatchIntent())
sb.add_request_handler(CompletedPetMatchIntent())
sb.add_request_handler(DogInfoIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(ExitIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())

# Add exception handler to the skill.
sb.add_exception_handler(CatchAllExceptionHandler())

# Add response interceptor to the skill.
sb.add_global_request_interceptor(RequestLogger())
sb.add_global_response_interceptor(ResponseLogger())

# Expose the lambda handler to register in AWS Lambda.
lambda_handler = sb.lambda_handler()

