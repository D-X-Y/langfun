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
"""Tests for language model."""

import os
import tempfile
from typing import Type
import unittest

import langfun.core as lf
from langfun.core.eval import base
from langfun.core.llms import fake
import langfun.core.structured as lf_structured
import pyglove as pg


# We put class definitions outside the functors just to make it easier
# to refer to them in test.
class Solution(pg.Object):
  final_answer: int


class SolutionForCompletion(pg.Object):
  question: str
  final_answer: int


@pg.functor
def answer_schema():
  return Solution


@pg.functor
def answer_schema_with_fewshot_examples():
  return Solution, [
      lf_structured.MappingExample(
          nl_text='The result of one plus two',
          schema=Solution,
          value=Solution(3)
      )
  ]


@pg.functor
def complete_schema():
  return SolutionForCompletion


def eval_set(
    eval_id: str,
    method: str,
    schema_fn,
    lm: lf.LanguageModel,
    use_cache: bool = True,
    cls: Type[base.Evaluation] = base.Evaluation,
    **kwargs,
):
  """Creates an evaluation object for testing."""
  tmp_dir = tempfile.gettempdir()
  return cls(
      id=eval_id,
      root_dir=tmp_dir,
      inputs=base.as_inputs([
          pg.Dict(question='Compute 1 + 1'),
          pg.Dict(question='Compute 1 + 2'),
      ]),
      method=method,
      prompt='{{example.question}}',
      completion_prompt_field='question',
      schema_fn=schema_fn,
      lm=lm,
      use_cache=use_cache,
      max_workers=1,
      **kwargs
  )


class EvaluationTest(unittest.TestCase):
  """Evaluation test."""

  def setUp(self):
    super().setUp()
    pg.symbolic.set_save_handler(pg.symbolic.default_save_handler)
    pg.symbolic.set_load_handler(pg.symbolic.default_load_handler)

  def test_basics(self):
    lm = fake.StaticSequence(['two', 'Solution(final_answer=2)'])
    s = eval_set('basic_test', 'call', schema_fn=answer_schema(), lm=lm)

    self.assertEqual(s.dir, os.path.join(s.root_dir, s.id))
    self.assertEqual(s.hash, s.clone().hash)
    # Test persistent hash.
    self.assertEqual(s.hash, 'c76d4fe6')
    self.assertEqual(
        s.hash, s.clone(override={'max_workers': 2, 'lm.timeout': 20}).hash
    )
    self.assertNotEqual(
        s.hash, s.clone(override={'prompt': 'Hello {{example.question}}'}).hash
    )
    self.assertIsNone(s.parent)
    self.assertIs(s.schema.spec.cls, Solution)
    self.assertIsNone(s.fewshot_examples)

    # Test schema_fn with fewshot examples.
    s.rebind(schema_fn=answer_schema_with_fewshot_examples())
    self.assertIs(s.schema.spec.cls, Solution)
    self.assertTrue(
        pg.eq(
            s.fewshot_examples,
            [
                lf_structured.MappingExample(
                    nl_text='The result of one plus two',
                    schema=Solution,
                    value=Solution(3))
            ]
        )
    )

  def test_schema_for_completion(self):

    @pg.functor()
    def _answer_schema():

      class Solution1(pg.Object):
        final_answer: int

      return Solution1, [
          lf.structured.MappingExample(
              nl_context='The result of one plus two',
              schema=Solution1,
              value=Solution1(3)
          )
      ]

    s = eval_set(
        'schema_for_completion', 'complete',
        schema_fn=_answer_schema(), lm=fake.StaticResponse('hi'))

    fewshot_examples = s.fewshot_examples
    solution_cls = s.schema.spec.cls

    # Verify class schema get updated.
    self.assertEqual('question', list(solution_cls.__schema__.keys())[0])

    # Verify query examples are mapped to completion examples.
    self.assertTrue(
        pg.eq(
            fewshot_examples,
            [
                lf.structured.MappingExample(
                    value=lf.structured.mapping.Pair(
                        left=solution_cls.partial(
                            question='The result of one plus two'),
                        right=solution_cls('The result of one plus two', 3),
                    )
                )
            ]
        )
    )

  def test_bad_init(self):
    with self.assertRaisesRegex(ValueError, '.*'):
      eval_set('bad_init1', 'complete', None, lm=fake.StaticResponse('hi'))

    @pg.functor()
    def _bad_completion_schema():
      return int

    s = eval_set(
        'bad_init2', 'complete',
        schema_fn=_bad_completion_schema(), lm=fake.StaticResponse('hi'))

    with self.assertRaisesRegex(TypeError, '.*must be .*class.*'):
      _ = s.schema

  def test_dryrun(self):
    lm = fake.StaticResponse('Solution(final_answer=2)')
    s = eval_set('dryrun_test', 'query', schema_fn=answer_schema(), lm=lm)
    s.dryrun(verbose=True)
    self.assertEqual(
        s.dryrun_output,
        lf.AIMessage(
            text='Solution(final_answer=2)',
            result=Solution(2),
            cache_seed=0,
            score=1.0,
            tags=['lm-response', 'lm-output', 'transformed'],
        ),
    )

  def test_run(self):
    lm = fake.StaticSequence([
        'Solution(final_answer=2)',
        '3',
    ])
    s = eval_set('run_test', 'query', schema_fn=answer_schema(), lm=lm)
    s.run()
    self.assertEqual(
        s.result,
        dict(
            experiment_setup=dict(
                id='run_test',
                dir=s.dir,
                model='StaticSequence',
                prompt_template='{{example.question}}',
                method='query',
                schema_fn='answer_schema()',
            ),
            cache_stats=dict(
                use_cache=True, num_queries=2, num_hits=0, num_updates=2
            ),
            metrics=dict(total=2, failures=1, failure_rate=0.5),
        ),
    )
    self.assertTrue(
        os.path.exists(os.path.join(s.dir, base.Evaluation.EXPERIMENT_JSON)))
    self.assertTrue(
        os.path.exists(os.path.join(s.dir, base.Evaluation.RESULT_JSON)))
    self.assertTrue(
        os.path.exists(os.path.join(s.dir, base.Evaluation.CACHE_JSON)))
    self.assertTrue(
        os.path.exists(os.path.join(s.root_dir, base.Evaluation.SUMMARY_HTML))
    )
    self.assertTrue(
        os.path.exists(os.path.join(s.dir, base.Evaluation.INDEX_HTML)))
    self.assertTrue(
        os.path.exists(os.path.join(s.dir, base.Evaluation.FAILURES_HTML)))

  def test_run_wihtout_save(self):
    lm = fake.StaticSequence([
        'Solution(final_answer=2)',
        '3',
    ])
    s = eval_set(
        'run_without_save_test', 'query', schema_fn=answer_schema(), lm=lm)
    s.run(save=False, show_progress=False)

    # Cache will always be saved
    self.assertTrue(
        os.path.exists(os.path.join(s.dir, base.Evaluation.CACHE_JSON)))
    self.assertFalse(
        os.path.exists(os.path.join(s.dir, base.Evaluation.EXPERIMENT_JSON)))
    self.assertFalse(
        os.path.exists(os.path.join(s.dir, base.Evaluation.RESULT_JSON)))
    self.assertFalse(
        os.path.exists(os.path.join(s.dir, base.Evaluation.INDEX_HTML)))
    self.assertFalse(
        os.path.exists(os.path.join(s.dir, base.Evaluation.FAILURES_HTML)))

  def test_load(self):
    lm = fake.StaticResponse('Solution(final_answer=2)')
    s = eval_set('loas_test', 'query', schema_fn=answer_schema(), lm=lm)
    s.run(dryrun=True)
    self.assertIsNotNone(s.result)

    s2 = base.load(s.dir)
    self.assertTrue(pg.eq(s, s2))
    self.assertIsNone(s2.result)
    s2.load_result()
    self.assertEqual(s2.result, s.result)

  def test_run_with_filter(self):
    lm = fake.StaticResponse('Solution(final_answer=2)')
    s = eval_set(
        'run_filter_test', pg.oneof(['call', 'query']),
        schema_fn=answer_schema(), lm=lm)
    self.assertEqual(
        s.run(filter=lambda x: x.method == 'query', dryrun=True, summary=False),
        {
            s.children[0].id: None,
            s.children[1].id: dict(
                experiment_setup=dict(
                    id=s.children[1].id,
                    dir=s.children[1].dir,
                    model='StaticResponse',
                    prompt_template='{{example.question}}',
                    method='query',
                    schema_fn='answer_schema()',
                ),
                cache_stats=dict(
                    use_cache=True, num_queries=2, num_hits=0, num_updates=2
                ),
                metrics=dict(total=2, failures=0, failure_rate=0.0),
            )
        },
    )

  def test_search_space(self):
    lm = fake.StaticSequence([
        'Solution(final_answer=2)',
        '3',
    ])
    s = base.Evaluation(
        id='search_space_test',
        root_dir=tempfile.gettempdir(),
        inputs=base.as_inputs([
            pg.Dict(question='Compute 1 + 1'),
            pg.Dict(question='Compute 1 + 2'),
        ]),
        method='query',
        prompt=pg.oneof([
            lf.Template('{{example.question}}'),
            lf.Template('Hello {{example.question}}'),
        ]),
        schema_fn=answer_schema(),
        lm=lm,
        use_cache=True,
        max_workers=1,
    )
    self.assertEqual(s.children[0].id, f'{s.id}@{s.children[0].hash}')
    self.assertEqual(
        s.children[0].dir, os.path.join(s.root_dir, s.children[0].id)
    )
    # Test persistent hash.
    self.assertEqual(s.hash, 'e987475a')

    summary = s.run(verbose=True)
    self.assertEqual(len(summary.evaluations), 2)

    self.assertEqual(
        s.result,
        {
            s.children[0].id: dict(
                experiment_setup=dict(
                    id=s.children[0].id,
                    dir=s.children[0].dir,
                    model='StaticSequence',
                    prompt_template='{{example.question}}',
                    method='query',
                    schema_fn='answer_schema()',
                ),
                cache_stats=dict(
                    use_cache=True, num_queries=2, num_hits=0, num_updates=2
                ),
                metrics=dict(total=2, failures=1, failure_rate=0.5),
            ),
            s.children[1].id: dict(
                experiment_setup=dict(
                    id=s.children[1].id,
                    dir=s.children[1].dir,
                    model='StaticSequence',
                    prompt_template='Hello {{example.question}}',
                    method='query',
                    schema_fn='answer_schema()',
                ),
                cache_stats=dict(
                    use_cache=True, num_queries=2, num_hits=0, num_updates=2
                ),
                metrics=dict(total=2, failures=1, failure_rate=0.5),
            ),
        },
    )

  def test_call(self):
    lm = fake.StaticSequence(['two'])
    s = eval_set('call_test1', 'call', schema_fn=None, lm=lm)
    self.assertEqual(s.process(s.examples[0]).text, 'two')

    lm = fake.StaticSequence(['two', 'Solution(final_answer=2)'])
    s = eval_set('call_test2', 'call', schema_fn=answer_schema(), lm=lm)
    self.assertEqual(s.process(s.examples[0]).result, Solution(2))

    lm = fake.StaticSequence(['two\n1', 'Solution(final_answer=2)'])

    class CallWithPostProcess(base.Evaluation):
      def call_postprocess(self, lm_response):
        return lm_response.split('\n')[0]

    s = eval_set(
        'call_test3', 'call',
        schema_fn=answer_schema(), lm=lm, cls=CallWithPostProcess,
    )
    self.assertEqual(s.process(s.examples[0]).lm_input.source.text, 'two')

  def test_query(self):
    lm = fake.StaticSequence(['Solution(final_answer=2)'])
    s = eval_set('query_test', 'query', schema_fn=answer_schema(), lm=lm)
    self.assertEqual(s.process(s.examples[0]).result, Solution(2))

    # Test query with fewshot examples.
    lm = fake.StaticSequence(['Solution(final_answer=2)'])
    s = eval_set(
        'basic_test',
        'query',
        schema_fn=answer_schema_with_fewshot_examples(),
        lm=lm,
    )
    m = s.process(s.examples[0])
    self.assertIn('The result of one plus two', m.lm_input.text)

  def test_complete(self):
    lm = fake.StaticSequence(
        ["SolutionForCompletion(question='Compute 1 + 1', final_answer=2)"]
    )
    s = eval_set(
        'complete_test', 'complete', schema_fn=complete_schema(), lm=lm
    )
    self.assertEqual(
        s.process(s.examples[0]).result,
        SolutionForCompletion('Compute 1 + 1', 2)
    )

    # Testing for using a query schema for completion.

    @pg.functor()
    def _answer_schema():

      class Solution2(pg.Object):
        answer: int

      return Solution2

    lm = fake.StaticSequence(
        ["Solution2(question='Compute 1 + 1', answer=2)"],
    )
    s = eval_set(
        'complete_test2', 'complete', schema_fn=_answer_schema(), lm=lm
    )
    self.assertEqual(s.process(s.examples[0]).result.answer, 2)


class SuiteTest(unittest.TestCase):
  """Suite test."""

  def test_run(self):
    lm = fake.StaticSequence([
        'Solution(final_answer=2)',
        '3',
    ])
    s = base.Suite(
        'suite_run_test',
        [
            eval_set('run_test_1', 'query', schema_fn=answer_schema(), lm=lm),
            # A suite of search space. Two of the sub-experiments are identical,
            # thus the result of run_test_2 would include only two keys.
            eval_set('run_test_2',
                     pg.oneof(['call', 'query']),
                     schema_fn=pg.oneof([answer_schema(), answer_schema()]),
                     lm=lm),
        ],
    )
    # Test for persistent hash.
    self.assertEqual(s.hash, 'bb86a963')
    s.run()
    expected = {
        s.children[0].id: dict(
            experiment_setup=dict(
                id=s.children[0].id,
                dir=s.children[0].dir,
                model='StaticSequence',
                prompt_template='{{example.question}}',
                method='query',
                schema_fn='answer_schema()',
            ),
            cache_stats=dict(
                use_cache=True, num_queries=2, num_hits=0, num_updates=2
            ),
            metrics=dict(total=2, failures=1, failure_rate=0.5),
        ),
        s.children[1].id: {
            s.children[1]
            .children[0]
            .id: dict(
                experiment_setup=dict(
                    id=s.children[1].children[0].id,
                    dir=s.children[1].children[0].dir,
                    model='StaticSequence',
                    prompt_template='{{example.question}}',
                    method='call',
                    schema_fn='answer_schema()',
                ),
                cache_stats=dict(
                    use_cache=True, num_queries=3, num_hits=0, num_updates=2
                ),
                metrics=dict(total=2, failures=2, failure_rate=1.0),
            ),
            s.children[1]
            .children[2]
            .id: dict(
                experiment_setup=dict(
                    id=s.children[1].children[2].id,
                    dir=s.children[1].children[2].dir,
                    model='StaticSequence',
                    prompt_template='{{example.question}}',
                    method='query',
                    schema_fn='answer_schema()',
                ),
                cache_stats=dict(
                    use_cache=True,
                    num_queries=2,
                    num_hits=0,
                    num_updates=2,
                ),
                metrics=dict(total=2, failures=1, failure_rate=0.5),
            ),
        },
    }
    self.assertEqual(s.result, expected)


class InputsFrom(unittest.TestCase):
  """Tests for inputs_from."""

  def setUp(self):
    super().setUp()
    pg.symbolic.set_save_handler(pg.symbolic.default_save_handler)
    pg.symbolic.set_load_handler(pg.symbolic.default_load_handler)

  def test_inputs_from_a_single_file(self):
    tmp_dir = tempfile.gettempdir()
    path = os.path.join(tmp_dir, 'input_file.json')
    pg.save([1, 2, 3], path)
    self.assertEqual(base.inputs_from(path)(), [1, 2, 3])

  def test_inputs_from_multiple_files(self):
    tmp_dir = tempfile.gettempdir()
    path1 = os.path.join(tmp_dir, 'input_file1.json')
    pg.save([1, 2, 3], path1)
    path2 = os.path.join(tmp_dir, 'input_file2.json')
    pg.save([4, 5, 6], path2)
    self.assertEqual(base.inputs_from([path1, path2])(), [1, 2, 3, 4, 5, 6])

  def test_as_inputs(self):
    self.assertEqual(base.as_inputs([1, 2, 3])(), [1, 2, 3])


class TaskA(base.Evaluation):
  pass


class TaskB(base.Evaluation):
  pass


class SummaryTest(unittest.TestCase):

  def _eval_set(self, root_dir):
    return base.Suite(id='select_test', children=[
        TaskA(
            id='task_a',
            inputs=base.as_inputs([
                pg.Dict(question='Compute 1 + 1'),
            ]),
            method=pg.oneof(['query', 'call']),
            prompt=pg.oneof([
                lf.Template('{{example.question}}'),
                lf.Template('Hello {{example.question}}'),
            ]),
            schema_fn=pg.oneof([
                answer_schema(),
                answer_schema_with_fewshot_examples(),
            ]),
            lm=pg.oneof([
                fake.StaticSequence(['3']),
                fake.StaticResponse('2'),
            ]),
            use_cache=True,
            max_workers=1,
        ),
        TaskB(
            id='task_b',
            inputs=base.as_inputs([
                pg.Dict(question='Compute 1 + 1'),
            ]),
            method=pg.oneof(['query', 'call']),
            prompt=pg.oneof([
                lf.Template('{{example.question}}'),
            ]),
            schema_fn=pg.oneof([
                answer_schema(),
            ]),
            lm=pg.oneof([
                fake.StaticSequence(['3']),
                fake.StaticResponse('2'),
            ]),
            use_cache=True,
            max_workers=1,
        ),
    ], root_dir=root_dir)

  def test_select(self):
    summary = self._eval_set(None).summary()
    self.assertEqual(len(summary), 2 * 2 * 2 * 2 + 2 * 1 * 1 * 2)

    # Select on task.
    self.assertEqual(len(summary.select(TaskA)), 2 * 2 * 2 * 2)
    self.assertEqual(len(summary.select(TaskB)), 2 * 1 * 1 * 2)

    # Select on LM.
    self.assertEqual(
        len(summary.select(lm=fake.StaticResponse)),
        2 * 2 * 2 * 1 + 2 * 1 * 1 * 1
    )
    self.assertEqual(
        len(summary.select(lm=fake.StaticSequence(['3']))),
        2 * 2 * 2 * 1 + 2 * 1 * 1 * 1
    )
    self.assertEqual(
        len(summary.select(lm=(fake.StaticSequence, fake.StaticResponse))),
        2 * 2 * 2 * 2 + 2 * 1 * 1 * 2
    )

    # Select on method.
    self.assertEqual(
        len(summary.select(method='call')),
        2 * 2 * 2 * 1 + 2 * 1 * 1 * 1
    )
    self.assertEqual(
        len(summary.select(method=('call', 'query'))),
        2 * 2 * 2 * 2 + 2 * 1 * 1 * 2
    )

    # Select on schema.
    self.assertEqual(
        len(summary.select(schema_fn=answer_schema())),
        2 * 2 * 2 * 1 + 2 * 1 * 1 * 2
    )
    self.assertEqual(
        len(summary.select(schema_fn=answer_schema_with_fewshot_examples())),
        2 * 2 * 2 * 1
    )
    self.assertEqual(
        len(summary.select(
            schema_fn=(
                answer_schema(), answer_schema_with_fewshot_examples()))),
        2 * 2 * 2 * 2 + 2 * 1 * 1 * 2
    )

    # Select on completed.
    self.assertEqual(len(summary.select(completed=True)), 0)
    self.assertEqual(
        len(summary.select(completed=False)), 2 * 2 * 2 * 2 + 2 * 1 * 1 * 2)

  def test_from_dirs(self):
    root_dir = os.path.join(tempfile.gettempdir(), 'from_dirs_test')
    s = self._eval_set(root_dir)
    s.run()
    self.assertEqual(
        len(base.Summary.from_dirs(root_dir)), 2 * 2 * 2 * 2 + 2 * 1 * 1 * 2
    )
    self.assertEqual(
        len(base.Summary.from_dirs(root_dir, 'task_b')), 2 * 1 * 1 * 2
    )
    self.assertEqual(
        len(base.Summary.from_dirs(root_dir, ('task_a'))), 2 * 2 * 2 * 2
    )

  def test_monitor(self):
    root_dir = os.path.join(tempfile.gettempdir(), 'monitor_test')
    s = self._eval_set(root_dir)
    s.run(summary=False)
    summary_file = os.path.join(root_dir, 'my_summary.html')
    summary = base.monitor(root_dir, summary_file)
    self.assertTrue(all(e.result for e in summary.evaluations))
    self.assertTrue(pg.io.path_exists(summary_file))

  def test_monitor_async(self):
    root_dir = os.path.join(tempfile.gettempdir(), 'monitor_async_test')
    pg.io.mkdirs(root_dir)
    summary_file = os.path.join(root_dir, 'my_summary.html')
    r = base.monitor_async(root_dir, summary_file, expect_new_dirs=True)
    self._eval_set(root_dir).run(summary=False)
    summary = r.stop()
    self.assertTrue(all(e.result for e in summary.evaluations))
    self.assertTrue(pg.io.path_exists(summary_file))


if __name__ == '__main__':
  unittest.main()
