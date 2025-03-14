# Copyright 2023 The Langfun Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for structured prompting."""

import inspect
import unittest

import langfun.core as lf
from langfun.core import coding
from langfun.core.llms import fake
from langfun.core.structured import mapping
from langfun.core.structured import prompting
from langfun.core.structured import schema as schema_lib
import pyglove as pg


class Activity(pg.Object):
  description: str


class Itinerary(pg.Object):
  day: pg.typing.Int[1, None]
  type: pg.typing.Enum['daytime', 'nighttime']
  activities: list[Activity]
  hotel: pg.typing.Str['.*Hotel'] | None


class QueryStructurePythonTest(unittest.TestCase):

  def test_render_no_examples(self):
    l = prompting.QueryStructurePython(int)
    m = lf.AIMessage('Compute 12 / 6 + 2.')

    self.assertEqual(
        l.render(user_prompt=m).text,
        inspect.cleandoc("""
            Please respond to the last USER_REQUEST with RESULT_OBJECT according to RESULT_TYPE.

            INSTRUCTIONS:
              1. Only response the required RESULT_OBJECT as illustrated by the given example.
              2. Don't add any comments in the response.
              3. RESULT_OBJECT must strictly follow the RESULT_TYPE.

            USER_REQUEST:
              1 + 1 =

            RESULT_TYPE:
              Answer

              ```python
              class Answer:
                final_answer: int
              ```

            RESULT_OBJECT:
              ```python
              Answer(final_answer=2)
              ```

            USER_REQUEST:
              Compute 12 / 6 + 2.

            RESULT_TYPE:
              int

            RESULT_OBJECT:
            """),
    )

  def test_render(self):
    l = prompting.QueryStructurePython(
        int,
        examples=[
            mapping.MappingExample('What is the answer of 1 plus 1?', None, 2),
            mapping.MappingExample(
                'Compute the value of 3 + (2 * 6).', None, 15
            ),
        ],
    )
    self.assertEqual(
        l.render(user_prompt=lf.AIMessage('Compute 12 / 6 + 2.')).text,
        inspect.cleandoc("""
            Please respond to the last USER_REQUEST with RESULT_OBJECT according to RESULT_TYPE.

            INSTRUCTIONS:
              1. Only response the required RESULT_OBJECT as illustrated by the given example.
              2. Don't add any comments in the response.
              3. RESULT_OBJECT must strictly follow the RESULT_TYPE.

            USER_REQUEST:
              1 + 1 =

            RESULT_TYPE:
              Answer

              ```python
              class Answer:
                final_answer: int
              ```

            RESULT_OBJECT:
              ```python
              Answer(final_answer=2)
              ```

            USER_REQUEST:
              What is the answer of 1 plus 1?

            RESULT_TYPE:
              int

            RESULT_OBJECT:
              ```python
              2
              ```

            USER_REQUEST:
              Compute the value of 3 + (2 * 6).

            RESULT_TYPE:
              int

            RESULT_OBJECT:
              ```python
              15
              ```


            USER_REQUEST:
              Compute 12 / 6 + 2.

            RESULT_TYPE:
              int

            RESULT_OBJECT:
            """),
    )

  def test_invocation(self):
    lm_input = lf.UserMessage('3-day itineraries to San Francisco')
    parse_structured_response = inspect.cleandoc(
        """
        ```python
        [
            Itinerary(
                day=1,
                type='daytime',
                activities=[
                    Activity(description='Arrive in San Francisco and check into your hotel.'),
                    Activity(description='Take a walk around Fisherman\\'s Wharf and have dinner at one of the many seafood restaurants.'),
                    Activity(description='Visit Pier 39 and see the sea lions.'),
                ], 
                hotel=None),
            Itinerary(
                day=2,
                type='daytime',
                activities=[
                    Activity(description='Take a ferry to Alcatraz Island and tour the infamous prison.'),
                    Activity(description='Take a walk across the Golden Gate Bridge.'),
                    Activity(description='Visit the Japanese Tea Garden in Golden Gate Park.'),
                ], 
                hotel=None),
            Itinerary(
                day=3,
                type='daytime',
                activities=[
                    Activity(description='Visit the de Young Museum and see the collection of American art.'),
                    Activity(description='Visit the San Francisco Museum of Modern Art.'),
                    Activity(description='Take a cable car ride.'),
                ], 
                hotel=None),
        ]
        ```
        """)
    with lf.context(
        lm=fake.StaticSequence(
            [parse_structured_response],
        ),
        override_attrs=True,
    ):
      l = prompting.QueryStructurePython(
          [Itinerary],
          examples=[
              mapping.MappingExample(
                  nl_context=inspect.cleandoc("""
                      Find the alternatives of expressing \"feeling great\".
                      """),
                  schema={'expression': str, 'words': list[str]},
                  value={
                      'expression': 'feeling great',
                      'words': [
                          'Ecstatic',
                          'Delighted',
                          'Wonderful',
                          'Enjoyable',
                          'Fantastic',
                      ],
                  },
              )
          ],
      )
      r = l(user_prompt=lm_input)
      self.assertEqual(len(r.result), 3)
      self.assertIsInstance(r.result[0], Itinerary)
      self.assertEqual(len(r.result[0].activities), 3)
      self.assertIsNone(r.result[0].hotel)

  def test_bad_response(self):
    with lf.context(
        lm=fake.StaticSequence(['a2']),
        override_attrs=True,
    ):
      with self.assertRaisesRegex(
          coding.CodeError,
          'name .* is not defined',
      ):
        prompting.query('Compute 1 + 2', int, autofix=0)

  def test_autofix(self):
    lm = fake.StaticSequence([
        '=1',
        inspect.cleandoc("""
            CodeCorrection(
                latest_code=CodeWithError(
                    code='=1',
                    error='SyntaxError: invalid syntax (<unknown> line 1)\\n: =1'
                ),
                correction_history=[],
                corrected_code='1',
            )
            """),
    ])
    self.assertEqual(prompting.query('what is 1 + 0', int, lm=lm), 1)

  def test_query(self):
    lm = fake.StaticSequence(['1'])
    self.assertEqual(prompting.query('what is 1 + 0', int, lm=lm), 1)

    # Testing calling the same `lm` without copy.
    with self.assertRaises(IndexError):
      prompting.query('what is 1 + 2', int, lm=lm)

    self.assertEqual(
        prompting.query(
            'what is 1 + 0', int, lm=lm.clone(), returns_message=True
        ),
        lf.AIMessage(
            '1',
            result=1,
            score=1.0,
            tags=['lm-response', 'lm-output', 'transformed'],
        ),
    )
    self.assertEqual(
        prompting.query(
            lf.Template('what is {{x}} + {{y}}'), int, x=1, y=0, lm=lm.clone()
        ),
        1,
    )


class QueryStructureJsonTest(unittest.TestCase):

  def test_render_no_examples(self):
    l = prompting.QueryStructureJson(int)
    m = lf.AIMessage('Compute 12 / 6 + 2.')

    self.assertEqual(
        l.render(user_prompt=m).text,
        inspect.cleandoc("""
            Please respond to the last USER_REQUEST with JSON according to SCHEMA:

            INSTRUCTIONS:
              1. If the schema has `_type`, carry it over to the JSON output.
              2. If a field from the schema cannot be extracted from the response, use null as the JSON value.

            USER_REQUEST:
              1 + 1 =

            SCHEMA:
              {"result": {"_type": "langfun.core.structured.prompting.Answer", "final_answer": int}}

            JSON:
              {"result": {"_type": "langfun.core.structured.prompting.Answer", "final_answer": 2}}

            USER_REQUEST:
              Compute 12 / 6 + 2.

            SCHEMA:
              {"result": int}

            JSON:
            """),
    )

  def test_render(self):
    l = prompting.QueryStructureJson(
        int,
        examples=[
            mapping.MappingExample('What is the answer of 1 plus 1?', None, 2),
            mapping.MappingExample(
                'Compute the value of 3 + (2 * 6).', None, 15
            ),
        ],
    )
    self.assertEqual(
        l.render(user_prompt=lf.AIMessage('Compute 12 / 6 + 2.')).text,
        inspect.cleandoc("""
            Please respond to the last USER_REQUEST with JSON according to SCHEMA:

            INSTRUCTIONS:
              1. If the schema has `_type`, carry it over to the JSON output.
              2. If a field from the schema cannot be extracted from the response, use null as the JSON value.

            USER_REQUEST:
              1 + 1 =

            SCHEMA:
              {"result": {"_type": "langfun.core.structured.prompting.Answer", "final_answer": int}}

            JSON:
              {"result": {"_type": "langfun.core.structured.prompting.Answer", "final_answer": 2}}

            USER_REQUEST:
              What is the answer of 1 plus 1?

            SCHEMA:
              {"result": int}

            JSON:
              {"result": 2}

            USER_REQUEST:
              Compute the value of 3 + (2 * 6).

            SCHEMA:
              {"result": int}

            JSON:
              {"result": 15}


            USER_REQUEST:
              Compute 12 / 6 + 2.

            SCHEMA:
              {"result": int}

            JSON:
            """),
    )

  def test_invocation(self):
    lm_input = lf.UserMessage('3-day itineraries to San Francisco')
    parse_structured_response = (
        lf.LangFunc(
            """
        {"result": [
          {
            "_type": {{itinerary_type}},
            "day": 1,
            "type": "daytime",
            "activities": [
              {
                "_type": {{activity_type}},
                "description": "Arrive in San Francisco and check into your hotel."
              },
              {
                "_type": {{activity_type}},
                "description": "Take a walk around Fisherman's Wharf and have dinner at one of the many seafood restaurants."
              },
              {
                "_type": {{activity_type}},
                "description": "Visit Pier 39 and see the sea lions."
              }
            ],
            "hotel": null
          },
          {
              "_type": {{itinerary_type}},
              "day": 2,
              "type": "daytime",
              "activities": [
                {
                  "_type": {{activity_type}},
                  "description": "Take a ferry to Alcatraz Island and tour the infamous prison."
                },
                {
                  "_type": {{activity_type}},
                  "description": "Take a walk across the Golden Gate Bridge."
                },
                {
                  "_type": {{activity_type}},
                  "description": "Visit the Japanese Tea Garden in Golden Gate Park."
                }
              ], 
              "hotel": null
           },
           {
              "_type": {{itinerary_type}},
              "day": 3,
              "type": "daytime",
              "activities": [
                {
                  "_type": {{activity_type}},
                  "description": "Visit the de Young Museum and see the collection of American art."
                },
                {
                  "_type": {{activity_type}},
                  "description": "Visit the San Francisco Museum of Modern Art."
                },
                {
                  "_type": {{activity_type}},
                  "description": "Take a cable car ride."
                }
              ],
              "hotel": null
            }
          ]}
        """,
            itinerary_type=f'"{Itinerary.__type_name__}"',
            activity_type=f'"{Activity.__type_name__}"',
        )
        .render()
        .text
    )
    with lf.context(
        lm=fake.StaticSequence(
            [parse_structured_response],
        ),
        override_attrs=True,
    ):
      l = prompting.QueryStructureJson(
          [Itinerary],
          examples=[
              mapping.MappingExample(
                  nl_context=inspect.cleandoc("""
                      Find the alternatives of expressing \"feeling great\".
                      """),
                  schema={'expression': str, 'words': list[str]},
                  value={
                      'expression': 'feeling great',
                      'words': [
                          'Ecstatic',
                          'Delighted',
                          'Wonderful',
                          'Enjoyable',
                          'Fantastic',
                      ],
                  },
              )
          ],
      )
      r = l(user_prompt=lm_input)
      self.assertEqual(len(r.result), 3)
      self.assertIsInstance(r.result[0], Itinerary)
      self.assertEqual(len(r.result[0].activities), 3)
      self.assertIsNone(r.result[0].hotel)

  def test_bad_transform(self):
    with lf.context(
        lm=fake.StaticSequence(['3']),
        override_attrs=True,
    ):
      with self.assertRaisesRegex(
          schema_lib.JsonError,
          'No JSON dict in the output',
      ):
        prompting.query('Compute 1 + 2', int, protocol='json')

  def test_query(self):
    lm = fake.StaticSequence(['{"result": 1}'])
    self.assertEqual(
        prompting.query('what is 1 + 0', int, lm=lm, protocol='json'), 1
    )


if __name__ == '__main__':
  unittest.main()
