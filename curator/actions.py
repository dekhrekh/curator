from .exceptions import *
from .utils import *
import logging
import time
from datetime import datetime

class Alias(object):
    def __init__(self, name=None, extra_settings={}, **kwargs):
        """
        Define the Alias object.

        :arg name: The alias name
        :arg extra_settings: Extra settings, including filters and routing. For
            more information see
            https://www.elastic.co/guide/en/elasticsearch/reference/current/indices-aliases.html
        :type extra_settings: dict, representing the settings.
        """
        if not name:
            raise MissingArgument('No value for "name" provided.')
        #: Instance variable
        #: The strftime parsed version of `name`.
        self.name = parse_date_pattern(name)
        #: The list of actions to perform.  Populated by
        #: :mod:`curator.actions.Alias.add` and
        #: :mod:`curator.actions.Alias.remove`
        self.actions = []
        #: Instance variable.
        #: The Elasticsearch Client object derived from `ilo`
        self.client  = None
        #: Instance variable.
        #: Any extra things to add to the alias, like filters, or routing.
        self.extra_settings = extra_settings
        self.loggit  = logging.getLogger('curator.actions.alias')

    def add(self, ilo, warn_if_no_indices=False):
        """
        Create `add` statements for each index in `ilo` for `alias`, then
        append them to `actions`.  Add any `extras` that may be there.

        :arg ilo: A :class:`curator.indexlist.IndexList` object

        """
        verify_index_list(ilo)
        if not self.client:
            self.client = ilo.client
        try:
            ilo.empty_list_check()
        except NoIndices:
            # Add a warning if there are no indices to add, if so set in options
            if warn_if_no_indices:
                self.loggit.warn(
                    'No indices found after processing filters. '
                    'Nothing to add to {0}'.format(self.name)
                )
                return
            else:
                # Re-raise the NoIndices so it will behave as before
                raise NoIndices
        for index in ilo.working_list():
            self.loggit.debug(
                'Adding index {0} to alias {1} with extra settings '
                '{2}'.format(index, self.name, self.extra_settings)
            )
            add_dict = { 'add' : { 'index' : index, 'alias': self.name } }
            add_dict['add'].update(self.extra_settings)
            self.actions.append(add_dict)

    def remove(self, ilo, warn_if_no_indices=False):
        """
        Create `remove` statements for each index in `ilo` for `alias`,
        then append them to `actions`.

        :arg ilo: A :class:`curator.indexlist.IndexList` object
        """
        verify_index_list(ilo)
        if not self.client:
            self.client = ilo.client
        try:
            ilo.empty_list_check()
        except NoIndices:
            # Add a warning if there are no indices to add, if so set in options
            if warn_if_no_indices:
                self.loggit.warn(
                    'No indices found after processing filters. '
                    'Nothing to remove from {0}'.format(self.name)
                )
                return
            else:
                # Re-raise the NoIndices so it will behave as before
                raise NoIndices
        aliases = self.client.indices.get_alias()
        for index in ilo.working_list():
            if index in aliases:
                self.loggit.debug(
                    'Index {0} in get_aliases output'.format(index))
                # Only remove if the index is associated with the alias
                if self.name in aliases[index]['aliases']:
                    self.loggit.debug(
                        'Removing index {0} from alias '
                        '{1}'.format(index, self.name)
                    )
                    self.actions.append(
                        { 'remove' : { 'index' : index, 'alias': self.name } })
                else:
                    self.loggit.debug(
                        'Can not remove: Index {0} is not associated with alias'
                        ' {1}'.format(index, self.name)
                    )

    def body(self):
        """
        Return a `body` string suitable for use with the `update_aliases` API
        call.
        """
        if not self.actions:
            raise ActionError('No "add" or "remove" operations')
        self.loggit.debug('Alias actions: {0}'.format(self.actions))

        return { 'actions' : self.actions }

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        self.loggit.info('DRY-RUN MODE.  No changes will be made.')
        for item in self.body()['actions']:
            job = list(item.keys())[0]
            index = item[job]['index']
            alias = item[job]['alias']
            # We want our log to look clever, so if job is "remove", strip the
            # 'e' so "remove" can become "removing".  "adding" works already.
            self.loggit.info(
                'DRY-RUN: alias: {0}ing index "{1}" {2} alias '
                '"{3}"'.format(
                    job.rstrip('e'),
                    index,
                    'to' if job is 'add' else 'from',
                    alias
                )
            )

    def do_action(self):
        """
        Run the API call `update_aliases` with the results of `body()`
        """
        self.loggit.info('Updating aliases...')
        self.loggit.info('Alias actions: {0}'.format(self.body()))
        try:
            self.client.indices.update_aliases(body=self.body())
        except Exception as e:
            report_failure(e)

class Allocation(object):
    def __init__(self, ilo, key=None, value=None, allocation_type='require',
        wait_for_completion=False, wait_interval=3, max_wait=-1,
        ):
        """
        :arg ilo: A :class:`curator.indexlist.IndexList` object
        :arg key: An arbitrary metadata attribute key.  Must match the key
            assigned to at least some of your nodes to have any effect.
        :arg value: An arbitrary metadata attribute value.  Must correspond to
            values associated with `key` assigned to at least some of your nodes
            to have any effect. If a `None` value is provided, it will remove
            any setting associated with that `key`.
        :arg allocation_type: Type of allocation to apply. Default is `require`
        :arg wait_for_completion: Wait (or not) for the operation
            to complete before returning.  (default: `False`)
        :type wait_for_completion: bool
        :arg wait_interval: How long in seconds to wait between checks for 
            completion.
        :arg max_wait: Maximum number of seconds to `wait_for_completion`

        .. note::
            See:
            https://www.elastic.co/guide/en/elasticsearch/reference/current/shard-allocation-filtering.html
        """
        verify_index_list(ilo)
        if not key:
            raise MissingArgument('No value for "key" provided')
        if allocation_type not in ['require', 'include', 'exclude']:
            raise ValueError(
                '{0} is an invalid allocation_type.  Must be one of "require", '
                '"include", "exclude".'.format(allocation_type)
            )
        #: Instance variable.
        #: Internal reference to `ilo`
        self.index_list = ilo
        #: Instance variable.
        #: The Elasticsearch Client object derived from `ilo`
        self.client     = ilo.client
        self.loggit     = logging.getLogger('curator.actions.allocation')
        #: Instance variable.
        #: Populated at instance creation time. Value is
        #: ``index.routing.allocation.`` `allocation_type` ``.`` `key` ``.`` `value`
        bkey = 'index.routing.allocation.{0}.{1}'.format(allocation_type, key)
        self.body       = { bkey : value }
        #: Instance variable.
        #: Internal reference to `wait_for_completion`
        self.wfc        = wait_for_completion
        #: Instance variable
        #: How many seconds to wait between checks for completion.
        self.wait_interval = wait_interval
        #: Instance variable.
        #: How long in seconds to `wait_for_completion` before returning with an
        #: exception. A value of -1 means wait forever.
        self.max_wait   = max_wait

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        show_dry_run(self.index_list, 'allocation', body=self.body)

    def do_action(self):
        """
        Change allocation settings for indices in `index_list.indices` with the
        settings in `body`.
        """
        self.loggit.debug(
            'Cannot get change shard routing allocation of closed indices.  '
            'Omitting any closed indices.'
        )
        self.index_list.filter_closed()
        self.index_list.empty_list_check()

        self.loggit.info('Updating index setting {0}'.format(self.body))
        try:
            index_lists = chunk_index_list(self.index_list.indices)
            for l in index_lists:
                self.client.indices.put_settings(
                    index=to_csv(l), body=self.body
                )
                if self.wfc:
                    logger.debug(
                        'Waiting for shards to complete relocation for indices:'
                        ' {0}'.format(to_csv(l))
                    )
                    wait_for_it(
                        self.client, 'allocation', 
                        wait_interval=self.wait_interval, max_wait=self.max_wait
                    )
        except Exception as e:
            report_failure(e)

class Close(object):
    def __init__(self, ilo, delete_aliases=False):
        """
        :arg ilo: A :class:`curator.indexlist.IndexList` object
        :arg delete_aliases: If `True`, will delete any associated aliases
            before closing indices.
        :type delete_aliases: bool
        """
        verify_index_list(ilo)
        #: Instance variable.
        #: Internal reference to `ilo`
        self.index_list = ilo
        #: Instance variable.
        #: Internal reference to `delete_aliases`
        self.delete_aliases = delete_aliases
        #: Instance variable.
        #: The Elasticsearch Client object derived from `ilo`
        self.client     = ilo.client
        self.loggit     = logging.getLogger('curator.actions.close')


    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        show_dry_run(
            self.index_list, 'close', **{'delete_aliases':self.delete_aliases})

    def do_action(self):
        """
        Close open indices in `index_list.indices`
        """
        self.index_list.filter_closed()
        self.index_list.empty_list_check()
        self.loggit.info(
            'Closing selected indices: {0}'.format(self.index_list.indices))
        try:
            index_lists = chunk_index_list(self.index_list.indices)
            for l in index_lists:
                if self.delete_aliases:
                    self.loggit.info(
                        'Deleting aliases from indices before closing.')
                    self.loggit.debug('Deleting aliases from: {0}'.format(l))
                    try:
                        self.client.indices.delete_alias(
                            index=to_csv(l), name='_all')
                    except Exception as e:
                        self.loggit.warn(
                            'Some indices may not have had aliases.  Exception:'
                            ' {0}'.format(e)
                        )
                self.client.indices.flush_synced(
                    index=to_csv(l), ignore_unavailable=True)
                self.client.indices.close(
                    index=to_csv(l), ignore_unavailable=True)
        except Exception as e:
            report_failure(e)

class ClusterRouting(object):
    def __init__(
        self, client, routing_type=None, setting=None, value=None,
        wait_for_completion=False, wait_interval=9, max_wait=-1
    ):
        """
        For now, the cluster routing settings are hardcoded to be ``transient``

        :arg client: An :class:`elasticsearch.Elasticsearch` client object
        :arg routing_type: Type of routing to apply. Either `allocation` or
            `rebalance`
        :arg setting: Currently, the only acceptable value for `setting` is
            ``enable``. This is here in case that changes.
        :arg value: Used only if `setting` is `enable`. Semi-dependent on
            `routing_type`. Acceptable values for `allocation` and `rebalance`
            are ``all``, ``primaries``, and ``none`` (string, not `NoneType`).
            If `routing_type` is `allocation`, this can also be
            ``new_primaries``, and if `rebalance`, it can be ``replicas``.
        :arg wait_for_completion: Wait (or not) for the operation
            to complete before returning.  (default: `False`)
        :type wait_for_completion: bool
        :arg wait_interval: How long in seconds to wait between checks for 
            completion.
        :arg max_wait: Maximum number of seconds to `wait_for_completion`
        """
        verify_client_object(client)
        #: Instance variable.
        #: An :class:`elasticsearch.Elasticsearch` client object
        self.client  = client
        self.loggit  = logging.getLogger('curator.actions.cluster_routing')
        #: Instance variable.
        #: Internal reference to `wait_for_completion`
        self.wfc     = wait_for_completion
        #: Instance variable
        #: How many seconds to wait between checks for completion.
        self.wait_interval = wait_interval
        #: Instance variable.
        #: How long in seconds to `wait_for_completion` before returning with an
        #: exception. A value of -1 means wait forever.
        self.max_wait   = max_wait

        if setting != 'enable':
            raise ValueError(
                'Invalid value for "setting": {0}.'.format(setting)
            )
        if routing_type == 'allocation':
            if value not in ['all', 'primaries', 'new_primaries', 'none']:
                raise ValueError(
                    'Invalid "value": {0} with "routing_type":'
                    '{1}.'.format(value, routing_type)
                )
        elif routing_type == 'rebalance':
            if value not in ['all', 'primaries', 'replicas', 'none']:
                raise ValueError(
                    'Invalid "value": {0} with "routing_type":'
                    '{1}.'.format(value, routing_type)
                )
        else:
            raise ValueError(
                'Invalid value for "routing_type": {0}.'.format(routing_type)
            )
        bkey = 'cluster.routing.{0}.{1}'.format(routing_type,setting)
        self.body = { 'transient' : { bkey : value } }

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        logger.info('DRY-RUN MODE.  No changes will be made.')
        self.loggit.info(
            'DRY-RUN: Update cluster routing settings with arguments: '
            '{0}'.format(self.body)
        )

    def do_action(self):
        """
        Change cluster routing settings with the settings in `body`.
        """
        self.loggit.info('Updating cluster settings: {0}'.format(self.body))
        try:
            self.client.cluster.put_settings(body=self.body)
            if self.wfc:
                logger.debug(
                    'Waiting for shards to complete routing and/or rebalancing'
                )
                wait_for_it(
                    self.client, 'cluster_routing', 
                    wait_interval=self.wait_interval, max_wait=self.max_wait
                )
        except Exception as e:
            report_failure(e)

class CreateIndex(object):
    def __init__(self, client, name, extra_settings={}):
        """
        :arg client: An :class:`elasticsearch.Elasticsearch` client object
        :arg name: A name, which can contain :py:func:`time.strftime`
            strings
        :arg extra_settings: The `settings` and `mappings` for the index. For
            more information see
            https://www.elastic.co/guide/en/elasticsearch/reference/current/indices-create-index.html
        :type extra_settings: dict, representing the settings and mappings.
        """
        verify_client_object(client)
        if not name:
            raise ConfigurationError('Value for "name" not provided.')
        #: Instance variable.
        #: The parsed version of `name`
        self.name       = parse_date_pattern(name)
        #: Instance variable.
        #: Extracted from the config yaml, it should be a dictionary of
        #: mappings and settings suitable for index creation.
        self.body       = extra_settings
        #: Instance variable.
        #: An :class:`elasticsearch.Elasticsearch` client object
        self.client     = client
        self.loggit     = logging.getLogger('curator.actions.create_index')

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        logger.info('DRY-RUN MODE.  No changes will be made.')
        self.loggit.info(
            'DRY-RUN: create_index "{0}" with arguments: '
            '{1}'.format(self.name, self.body)
        )

    def do_action(self):
        """
        Create index identified by `name` with settings in `body`
        """
        self.loggit.info(
            'Creating index "{0}" with settings: '
            '{1}'.format(self.name, self.body)
        )
        try:
            self.client.indices.create(index=self.name, body=self.body)
        except Exception as e:
            report_failure(e)

class DeleteIndices(object):
    def __init__(self, ilo, master_timeout=30):
        """
        :arg ilo: A :class:`curator.indexlist.IndexList` object
        :arg master_timeout: Number of seconds to wait for master node response
        """
        verify_index_list(ilo)
        if not type(master_timeout) == type(int()):
            raise TypeError(
                'Incorrect type for "master_timeout": {0}. '
                'Should be integer value.'.format(type(master_timeout))
            )
        #: Instance variable.
        #: Internal reference to `ilo`
        self.index_list     = ilo
        #: Instance variable.
        #: The Elasticsearch Client object derived from `ilo`
        self.client         = ilo.client
        #: Instance variable.
        #: String value of `master_timeout` + 's', for seconds.
        self.master_timeout = str(master_timeout) + 's'
        self.loggit         = logging.getLogger('curator.actions.delete_indices')
        self.loggit.debug('master_timeout value: {0}'.format(
            self.master_timeout))

    def _verify_result(self, result, count):
        """
        Breakout method to aid readability
        :arg result: A list of indices from `_get_result_list`
        :arg count: The number of tries that have occurred
        :rtype: bool
        """
        if len(result) > 0:
            self.loggit.error(
                'The following indices failed to delete on try '
                '#{0}:'.format(count)
            )
            for idx in result:
                self.loggit.error("---{0}".format(idx))
            return False
        else:
            self.loggit.debug(
                'Successfully deleted all indices on try #{0}'.format(count)
            )
            return True

    def __chunk_loop(self, chunk_list):
        """
        Loop through deletes 3 times to ensure they complete
        :arg chunk_list: A list of indices pre-chunked so it won't overload the
            URL size limit.
        """
        working_list = chunk_list
        for count in range(1, 4): # Try 3 times
            for i in working_list:
                self.loggit.info("---deleting index {0}".format(i))
            self.client.indices.delete(
                index=to_csv(working_list), master_timeout=self.master_timeout)
            result = [ i for i in working_list if i in get_indices(self.client)]
            if self._verify_result(result, count):
                return
            else:
                working_list = result
        self.loggit.error(
            'Unable to delete the following indices after 3 attempts: '
            '{0}'.format(result)
        )

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        show_dry_run(self.index_list, 'delete_indices')

    def do_action(self):
        """
        Delete indices in `index_list.indices`
        """
        self.index_list.empty_list_check()
        self.loggit.info(
            'Deleting selected indices: {0}'.format(self.index_list.indices))
        try:
            index_lists = chunk_index_list(self.index_list.indices)
            for l in index_lists:
                self.__chunk_loop(l)
        except Exception as e:
            report_failure(e)

class ForceMerge(object):
    def __init__(self, ilo, max_num_segments=None, delay=0):
        """
        :arg ilo: A :class:`curator.indexlist.IndexList` object
        :arg max_num_segments: Number of segments per shard to forceMerge
        :arg delay: Number of seconds to delay between forceMerge operations
        """
        verify_index_list(ilo)
        if not max_num_segments:
            raise MissingArgument('Missing value for "max_num_segments"')
        #: Instance variable.
        #: The Elasticsearch Client object derived from `ilo`
        self.client = ilo.client
        #: Instance variable.
        #: Internal reference to `ilo`
        self.index_list = ilo
        #: Instance variable.
        #: Internally accessible copy of `max_num_segments`
        self.max_num_segments = max_num_segments
        #: Instance variable.
        #: Internally accessible copy of `delay`
        self.delay = delay
        self.loggit = logging.getLogger('curator.actions.forcemerge')

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        show_dry_run(
            self.index_list, 'forcemerge',
            max_num_segments=self.max_num_segments,
            delay=self.delay,
        )

    def do_action(self):
        """
        forcemerge indices in `index_list.indices`
        """
        self.index_list.empty_list_check()
        self.index_list.filter_forceMerged(
            max_num_segments=self.max_num_segments)
        self.loggit.info('forceMerging selected indices')
        try:
            for index_name in self.index_list.indices:
                self.loggit.info(
                    'forceMerging index {0} to {1} segments per shard.  '
                    'Please wait...'.format(index_name, self.max_num_segments)
                )
                self.client.indices.forcemerge(index=index_name,
                    max_num_segments=self.max_num_segments)
                if self.delay > 0:
                    self.loggit.info(
                        'Pausing for {0} seconds before continuing...'.format(
                            self.delay)
                    )
                    time.sleep(self.delay)
        except Exception as e:
            report_failure(e)

class Open(object):
    def __init__(self, ilo):
        """
        :arg ilo: A :class:`curator.indexlist.IndexList` object
        """
        verify_index_list(ilo)
        #: Instance variable.
        #: The Elasticsearch Client object derived from `ilo`
        self.client     = ilo.client
        #: Instance variable.
        #: Internal reference to `ilo`
        self.index_list = ilo
        self.loggit     = logging.getLogger('curator.actions.open')

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        show_dry_run(self.index_list, 'open')

    def do_action(self):
        """
        Open closed indices in `index_list.indices`
        """
        self.index_list.empty_list_check()
        self.loggit.info(
            'Opening selected indices: {0}'.format(self.index_list.indices))
        try:
            index_lists = chunk_index_list(self.index_list.indices)
            for l in index_lists:
                self.client.indices.open(index=to_csv(l))
        except Exception as e:
            report_failure(e)

class Replicas(object):
    def __init__(self, ilo, count=None, wait_for_completion=False, 
        wait_interval=9, max_wait=-1):
        """
        :arg ilo: A :class:`curator.indexlist.IndexList` object
        :arg count: The count of replicas per shard
        :arg wait_for_completion: Wait (or not) for the operation
            to complete before returning.  (default: `False`)
        :type wait_for_completion: bool
        :arg wait_interval: How long in seconds to wait between checks for 
            completion.
        :arg max_wait: Maximum number of seconds to `wait_for_completion`
        """
        verify_index_list(ilo)
        # It's okay for count to be zero
        if count == 0:
            pass
        elif not count:
            raise MissingArgument('Missing value for "count"')
        #: Instance variable.
        #: The Elasticsearch Client object derived from `ilo`
        self.client     = ilo.client
        #: Instance variable.
        #: Internal reference to `ilo`
        self.index_list = ilo
        #: Instance variable.
        #: Internally accessible copy of `count`
        self.count      = count
        #: Instance variable.
        #: Internal reference to `wait_for_completion`
        self.wfc        = wait_for_completion
        #: Instance variable
        #: How many seconds to wait between checks for completion.
        self.wait_interval = wait_interval
        #: Instance variable.
        #: How long in seconds to `wait_for_completion` before returning with an
        #: exception. A value of -1 means wait forever.
        self.max_wait   = max_wait
        self.loggit     = logging.getLogger('curator.actions.replicas')

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        show_dry_run(self.index_list, 'replicas', count=self.count)

    def do_action(self):
        """
        Update the replica count of indices in `index_list.indices`
        """
        self.index_list.empty_list_check()
        self.loggit.debug(
            'Cannot get update replica count of closed indices.  '
            'Omitting any closed indices.'
        )
        self.index_list.filter_closed()
        self.loggit.info(
            'Setting the replica count to {0} for indices: '
            '{1}'.format(self.count, self.index_list.indices)
        )
        try:
            index_lists = chunk_index_list(self.index_list.indices)
            for l in index_lists:
                self.client.indices.put_settings(index=to_csv(l),
                    body={'number_of_replicas' : self.count})
                if self.wfc and self.count > 0:
                    logger.debug(
                        'Waiting for shards to complete replication for '
                        'indices: {0}'.format(to_csv(l))
                    )
                    wait_for_it(
                        self.client, 'replicas', 
                        wait_interval=self.wait_interval, max_wait=self.max_wait
                    )
        except Exception as e:
            report_failure(e)

class Rollover(object):
    def __init__(
            self, client, name, conditions, extra_settings=None,
            wait_for_active_shards=1
        ):
        """
        :arg client: An :class:`elasticsearch.Elasticsearch` client object
        :arg name: The name of the single-index-mapped alias to test for
            rollover conditions.
        :arg conditions: A dictionary of conditions to test
        :arg extra_settings: Must be either `None`, or a dictionary of settings
            to apply to the new index on rollover. This is used in place of
            `settings` in the Rollover API, mostly because it's already existent
            in other places here in Curator
        :arg wait_for_active_shards: The number of shards expected to be active
            before returning.
        """
        verify_client_object(client)
        self.loggit     = logging.getLogger('curator.actions.rollover')
        if not isinstance(conditions, dict):
            raise ConfigurationError('"conditions" must be a dictionary')
        else:
            self.loggit.debug('"conditions" is {0}'.format(conditions))
        if not isinstance(extra_settings, dict) and extra_settings is not None:
            raise ConfigurationError(
                '"extra_settings" must be a dictionary or None')
        #: Instance variable.
        #: The Elasticsearch Client object
        self.client     = client
        #: Instance variable.
        #: Internal reference to `conditions`
        self.conditions = conditions
        #: Instance variable.
        #: Internal reference to `extra_settings`
        self.settings   = extra_settings
        #: Instance variable.
        #: Internal reference to `wait_for_active_shards`
        self.wait_for_active_shards = wait_for_active_shards

        # Verify that `conditions` and `settings` are good?
        # Verify that `name` is an alias, and is only mapped to one index.
        if rollable_alias(client, name):
            self.name = name
        else:
            raise ValueError(
                    'Unable to perform index rollover with alias '
                    '"{0}". See previous logs for more details.'.format(name)
                )

    def body(self):
        """
        Create a body from conditions and settings
        """
        retval = {}
        retval['conditions'] = self.conditions
        if self.settings:
            retval['settings'] = self.settings
        return retval

    def doit(self, dry_run=False):
        """
        This exists solely to prevent having to have duplicate code in both
        `do_dry_run` and `do_action`
        """
        return self.client.indices.rollover(
            alias=self.name,
            body=self.body(),
            dry_run=dry_run,
            wait_for_active_shards=self.wait_for_active_shards,
        )

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        logger.info('DRY-RUN MODE.  No changes will be made.')
        result = self.doit(dry_run=True)
        logger.info('DRY-RUN: rollover: {0} result: '
            '{1}'.format(self.name, result))

    def do_action(self):
        """
        Rollover the index referenced by alias `name`
        """
        self.loggit.info('Performing index rollover')
        try:
            self.doit()
        except Exception as e:
            report_failure(e)

class DeleteSnapshots(object):
    def __init__(self, slo, retry_interval=120, retry_count=3):
        """
        :arg slo: A :class:`curator.snapshotlist.SnapshotList` object
        :arg retry_interval: Number of seconds to delay betwen retries. Default:
            120 (seconds)
        :arg retry_count: Number of attempts to make. Default: 3
        """
        verify_snapshot_list(slo)
        #: Instance variable.
        #: The Elasticsearch Client object derived from `slo`
        self.client         = slo.client
        #: Instance variable.
        #: Internally accessible copy of `retry_interval`
        self.retry_interval = retry_interval
        #: Instance variable.
        #: Internally accessible copy of `retry_count`
        self.retry_count    = retry_count
        #: Instance variable.
        #: Internal reference to `slo`
        self.snapshot_list  = slo
        #: Instance variable.
        #: The repository name derived from `slo`
        self.repository     = slo.repository
        self.loggit = logging.getLogger('curator.actions.delete_snapshots')

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        logger.info('DRY-RUN MODE.  No changes will be made.')
        mykwargs = {
            'repository' : self.repository,
            'retry_interval' : self.retry_interval,
            'retry_count' : self.retry_count,
        }
        for snap in self.snapshot_list.snapshots:
            logger.info('DRY-RUN: delete_snapshot: {0} with arguments: '
                '{1}'.format(snap, mykwargs))

    def do_action(self):
        """
        Delete snapshots in `slo`
        Retry up to `retry_count` times, pausing `retry_interval`
        seconds between retries.
        """
        self.snapshot_list.empty_list_check()
        self.loggit.info('Deleting selected snapshots')
        if not safe_to_snap(
            self.client, repository=self.repository,
            retry_interval=self.retry_interval, retry_count=self.retry_count):
                raise FailedExecution(
                    'Unable to delete snapshot(s) because a snapshot is in '
                    'state "IN_PROGRESS"')
        try:
            for s in self.snapshot_list.snapshots:
                self.loggit.info('Deleting snapshot {0}...'.format(s))
                self.client.snapshot.delete(
                    repository=self.repository, snapshot=s)
        except Exception as e:
            report_failure(e)

class Reindex(object):
    def __init__(self, ilo, request_body, refresh=True, 
        requests_per_second=-1, slices=1, timeout=60, wait_for_active_shards=1,
        wait_for_completion=True, max_wait=-1, wait_interval=9,
        remote_url_prefix=None, remote_ssl_no_validate=None, 
        remote_certificate=None, remote_client_cert=None, 
        remote_client_key=None, remote_aws_key=None, remote_aws_secret_key=None, 
        remote_aws_region=None, remote_filters={}):
        """
        :arg ilo: A :class:`curator.indexlist.IndexList` object
        :arg request_body: The body to send to 
            :class:`elasticsearch.Indices.Reindex`, which must be complete and 
            usable, as Curator will do no vetting of the request_body. If it 
            fails to function, Curator will return an exception.
        :arg refresh: Whether to refresh the entire target index after the 
            operation is complete. (default: `True`)
        :type refresh: bool
        :arg requests_per_second: The throttle to set on this request in 
            sub-requests per second. ``-1`` means set no throttle as does 
            ``unlimited`` which is the only non-float this accepts. (default: 
            ``-1``)
        :arg slices: The number of slices this task  should be divided into. 1 
            means the task will not be sliced into subtasks. (default: ``1``)
        :arg timeout: The length in seconds each individual bulk request should 
            wait for shards that are unavailable. (default: ``60``)
        :arg wait_for_active_shards: Sets the number of shard copies that must
            be active before proceeding with the reindex operation. (default:
            ``1``) means the primary shard only. Set to ``all`` for all shard
            copies, otherwise set to any non-negative value less than or equal
            to the total number of copies for the shard (number of replicas + 1)
        :arg wait_for_completion: Wait (or not) for the operation
            to complete before returning.  (default: `True`)
        :type wait_for_completion: bool
        :arg wait_interval: How long in seconds to wait between checks for 
            completion.
        :arg max_wait: Maximum number of seconds to `wait_for_completion`
        :arg remote_url_prefix: `Optional` url prefix, if needed to reach the 
            Elasticsearch API (i.e., it's not at the root level)
        :type remote_url_prefix: str
        :arg remote_ssl_no_validate: If `True`, do not validate the certificate
            chain.  This is an insecure option and you will see warnings in the
            log output.
        :type remote_ssl_no_validate: bool          
        :arg remote_certificate: Path to SSL/TLS certificate
        :arg remote_client_cert: Path to SSL/TLS client certificate (public key)
        :arg remote_client_key: Path to SSL/TLS private key
        :arg remote_aws_key: AWS IAM Access Key (Only used if the 
            :mod:`requests-aws4auth` python module is installed)
        :arg remote_aws_secret_key: AWS IAM Secret Access Key (Only used if the
            :mod:`requests-aws4auth` python module is installed)
        :arg remote_aws_region: AWS Region (Only used if the 
            :mod:`requests-aws4auth` python module is installed)
        :arg remote_filters: Apply these filters to the remote client for
            remote index selection.  
        """
        self.loggit = logging.getLogger('curator.actions.reindex')
        verify_index_list(ilo)
        # Normally, we'd check for an empty list here.  But since we can reindex
        # from remote, we might just be starting with an empty one. 
        # ilo.empty_list_check()
        if not isinstance(request_body, dict):
            raise ConfigurationError('"request_body" is not of type dictionary')
        #: Instance variable.
        #: Internal reference to `request_body`
        self.body = request_body
        self.loggit.debug('REQUEST_BODY = {0}'.format(request_body))
        #: Instance variable.
        #: The Elasticsearch Client object derived from `ilo`
        self.client = ilo.client
        #: Instance variable.
        #: Internal reference to `ilo`
        self.index_list = ilo
        #: Instance variable.
        #: Internal reference to `refresh`
        self.refresh = refresh
        #: Instance variable.
        #: Internal reference to `requests_per_second`
        self.requests_per_second = requests_per_second
        #: Instance variable.
        #: Internal reference to `slices`
        self.slices = slices
        #: Instance variable.
        #: Internal reference to `timeout`, and add "s" for seconds.
        self.timeout = '{0}s'.format(timeout)
        #: Instance variable.
        #: Internal reference to `wait_for_active_shards`
        self.wait_for_active_shards = wait_for_active_shards
        #: Instance variable.
        #: Internal reference to `wait_for_completion`
        self.wfc = wait_for_completion
        #: Instance variable
        #: How many seconds to wait between checks for completion.
        self.wait_interval = wait_interval
        #: Instance variable.
        #: How long in seconds to `wait_for_completion` before returning with an
        #: exception. A value of -1 means wait forever.
        self.max_wait   = max_wait
        # This is for error logging later...

        self.remote = False
        if 'remote' in self.body['source']:
            self.remote = True

        # REINDEX_SELECTION is the designated token.  If you use this for the
        # source "index," it will be replaced with the list of indices from the
        # provided 'ilo' (index list object).
        if self.body['source']['index'] == 'REINDEX_SELECTION' \
                and 'remote' not in self.body['source']:
            self.body['source']['index'] = self.index_list.indices        

        # Remote section
        
        elif 'remote' in self.body['source']:
            self.loggit.debug('Remote reindex request detected')
            if 'host' not in self.body['source']['remote']:
                raise ConfigurationError('Missing remote "host"')
            rclient_info = {}
            for k in ['host', 'username', 'password']:
                rclient_info[k] = self.body['source']['remote'][k] \
                    if k in self.body['source']['remote'] else None
            rhost = rclient_info['host']
            try:
                # Save these for logging later
                a = rhost.split(':')
                self.remote_port = a[2]
                self.remote_host = a[1][2:]
            except Exception as e:
                raise ConfigurationError(
                    'Host must be in the form [scheme]://[host]:[port] but '
                    'was [{0}]'.format(rhost)
                )
            rhttp_auth = '{0}:{1}'.format(
                    rclient_info['username'],rclient_info['password']) \
                if (rclient_info['username'] and rclient_info['password']) \
                    else None
            if rhost[:5] == 'http:':
                use_ssl = False
            elif rhost[:5] == 'https':
                use_ssl = True
            else:
                raise ConfigurationError(
                    'Host must be in URL format. You provided: '
                    '{0}'.format(rclient_info['host'])
                )

            # The rest only applies if using filters for remote indices
            if self.body['source']['index'] == 'REINDEX_SELECTION':
                self.loggit.debug('Filtering indices from remote')
                from .indexlist import IndexList
                self.loggit.debug('Remote client args: '
                    'host={0} '
                    'http_auth={1} '
                    'url_prefix={2} '
                    'use_ssl={3} '
                    'ssl_no_validate={4} '
                    'certificate={5} '
                    'client_cert={6} '
                    'client_key={7} '
                    'aws_key={8} '
                    'aws_secret_key={9} '
                    'aws_region={10}'.format(
                        rhost,
                        rhttp_auth,
                        remote_url_prefix,
                        use_ssl,
                        remote_ssl_no_validate,
                        remote_certificate,
                        remote_client_cert,
                        remote_client_key,
                        remote_aws_key,
                        remote_aws_secret_key,
                        remote_aws_region
                    )
                )

                try: # let's try to build a remote connection with these!
                    rclient = get_client(
                        host=rhost, 
                        http_auth=rhttp_auth,
                        url_prefix=remote_url_prefix,
                        use_ssl=use_ssl,
                        ssl_no_validate=remote_ssl_no_validate,
                        certificate=remote_certificate,
                        client_cert=remote_client_cert,
                        client_key=remote_client_key,
                        aws_key=remote_aws_key,
                        aws_secret_key=remote_aws_secret_key,
                        aws_region=remote_aws_region,
                    )
                except Exception as e:
                    self.loggit.error(
                        'Unable to establish connection to remote Elasticsearch'
                        ' with provided credentials/certificates/settings.'
                    )
                    report_failure(e)
                try:    
                    rio = IndexList(rclient)
                    rio.iterate_filters({'filters': remote_filters})
                    try:
                        rio.empty_list_check()
                    except NoIndices:
                        raise FailedExecution(
                            'No actionable remote indices selected after '
                            'applying filters.'
                        )
                    self.body['source']['index'] = rio.indices
                except Exception as e:
                    self.loggit.error(
                        'Unable to get/filter list of remote indices.'
                    )
                    report_failure(e)

        self.loggit.debug(
            'Reindexing indices: {0}'.format(self.body['source']['index']))

    def show_run_args(self):
        """
        Show what will run
        """
        return ('request body: {0} with arguments: '
            'refresh={1} '
            'requests_per_second={2} '
            'slices={3} '
            'timeout={4} '
            'wait_for_active_shards={5} '
            'wait_for_completion={6}'.format(
                self.body,
                self.refresh,
                self.requests_per_second,
                self.slices,
                self.timeout,
                self.wait_for_active_shards,
                self.wfc
            )
        )

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        self.loggit.info('DRY-RUN MODE.  No changes will be made.')
        self.loggit.info('DRY-RUN: REINDEX: {0}'.format(self.show_run_args()))

    def do_action(self):
        """
        Execute :class:`elasticsearch.Elasticsearch.reindex` operation with the
        provided request_body and arguments.
        """
        try:
            self.loggit.info('Commencing reindex operation')
            self.loggit.debug('REINDEX: {0}'.format(self.show_run_args()))
            # Always set wait_for_completion to False. Let 'wait_for_it' do its
            # thing if wait_for_completion is set to True. Report the task_id
            # either way.
            version = get_version(self.client)
            if version >= (5,1,0):
                response = self.client.reindex(
                    body=self.body, refresh=self.refresh, 
                    requests_per_second=self.requests_per_second, 
                    slices=self.slices,
                    timeout=self.timeout,
                    wait_for_active_shards=self.wait_for_active_shards,
                    wait_for_completion=False
                )
            else: # No slices for you
                self.loggit.info(
                    'Your version of elasticsearch ({0}) does not support '
                    'sliced scroll for reindex, so that setting will not be '
                    'used'.format(version)
                )
                response = self.client.reindex(
                    body=self.body, refresh=self.refresh, 
                    requests_per_second=self.requests_per_second, 
                    timeout=self.timeout,
                    wait_for_active_shards=self.wait_for_active_shards,
                    wait_for_completion=False
                )
            self.loggit.debug('TASK ID = {0}'.format(response['task']))
            if self.wfc:
                wait_for_it(
                    self.client, 'reindex', task_id=response['task'], 
                    wait_interval=self.wait_interval, max_wait=self.max_wait
                )
                # Verify the destination index is there after the fact
                post_run = get_indices(self.client)
                if self.body['dest']['index'] not in post_run:
                    self.loggit.error(
                        'Index "{0}" not found after reindex operation. Check '
                        'Elasticsearch logs for more '
                        'information.'.format(self.body['dest']['index'])
                    )
                    if self.remote:
                        self.loggit.error(
                            'Did you forget to add "reindex.remote.whitelist: '
                            '{0}:{1}" to the elasticsearch.yml file on the '
                            '"dest" node?'.format(
                                self.remote_host, self.remote_port
                            )
                        )
                    raise FailedExecution(
                        'Reindex failed. Index "{0}" not found.'.format(
                            self.body['dest']['index'])
                    )
                
            else:
                self.loggit.warn(
                    '"wait_for_completion" set to {0}.'
                    'Remember to check task_id "{1}" for successful completion '
                    'manually.'.format(self.wfc, response['task'])
                )
        except Exception as e:
            report_failure(e)


class Snapshot(object):
    def __init__(self, ilo, repository=None, name=None,
                ignore_unavailable=False, include_global_state=True,
                partial=False, wait_for_completion=True, wait_interval=9,
                max_wait=-1, skip_repo_fs_check=False):
        """
        :arg ilo: A :class:`curator.indexlist.IndexList` object
        :arg repository: The Elasticsearch snapshot repository to use
        :arg name: What to name the snapshot.
        :arg wait_for_completion: Wait (or not) for the operation
            to complete before returning.  (default: `True`)
        :type wait_for_completion: bool
        :arg wait_interval: How long in seconds to wait between checks for 
            completion.
        :arg max_wait: Maximum number of seconds to `wait_for_completion`
        :arg ignore_unavailable: Ignore unavailable shards/indices.
            (default: `False`)
        :type ignore_unavailable: bool
        :arg include_global_state: Store cluster global state with snapshot.
            (default: `True`)
        :type include_global_state: bool
        :arg partial: Do not fail if primary shard is unavailable. (default:
            `False`)
        :type partial: bool
        :arg skip_repo_fs_check: Do not validate write access to repository on
            all cluster nodes before proceeding. (default: `False`).  Useful for
            shared filesystems where intermittent timeouts can affect
            validation, but won't likely affect snapshot success.
        :type skip_repo_fs_check: bool
        """
        verify_index_list(ilo)
        # Check here and don't bother with the rest of this if there are no
        # indices in the index list.
        ilo.empty_list_check()
        if not repository_exists(ilo.client, repository=repository):
            raise ActionError(
                'Cannot snapshot indices to missing repository: '
                '{0}'.format(repository)
            )
        if not name:
            raise MissingArgument('No value for "name" provided.')
        #: Instance variable.
        #: The parsed version of `name`
        self.name = parse_date_pattern(name)
        #: Instance variable.
        #: The Elasticsearch Client object derived from `ilo`
        self.client              = ilo.client
        #: Instance variable.
        #: Internal reference to `ilo`
        self.index_list = ilo
        #: Instance variable.
        #: Internally accessible copy of `repository`
        self.repository          = repository
        #: Instance variable.
        #: Internally accessible copy of `wait_for_completion`
        self.wait_for_completion = wait_for_completion
        #: Instance variable
        #: How many seconds to wait between checks for completion.
        self.wait_interval = wait_interval
        #: Instance variable.
        #: How long in seconds to `wait_for_completion` before returning with an
        #: exception. A value of -1 means wait forever.
        self.max_wait   = max_wait
        #: Instance variable.
        #: Internally accessible copy of `skip_repo_fs_check`
        self.skip_repo_fs_check  = skip_repo_fs_check
        self.state               = None

        #: Instance variable.
        #: Populated at instance creation time by calling
        #: :mod:`curator.utils.create_snapshot_body` with `ilo.indices` and the
        #: provided arguments: `ignore_unavailable`, `include_global_state`,
        #: `partial`
        self.body                = create_snapshot_body(
                ilo.indices,
                ignore_unavailable=ignore_unavailable,
                include_global_state=include_global_state,
                partial=partial
            )

        self.loggit = logging.getLogger('curator.actions.snapshot')

    def get_state(self):
        """
        Get the state of the snapshot
        """
        try:
            self.state = self.client.snapshot.get(
                repository=self.repository,
                snapshot=self.name)['snapshots'][0]['state']
            return self.state
        except IndexError:
            raise CuratorException(
                'Snapshot "{0}" not found in repository '
                '"{1}"'.format(self.name, self.repository)
            )

    def report_state(self):
        """
        Log the state of the snapshot
        """
        self.get_state()
        if self.state == 'SUCCESS':
            self.loggit.info(
                'Snapshot {0} successfully completed.'.format(self.name))
        else:
            self.loggit.warn(
                'Snapshot {0} completed with state: {0}'.format(self.state))

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        self.loggit.info('DRY-RUN MODE.  No changes will be made.')
        self.loggit.info(
            'DRY-RUN: snapshot: {0} in repository {1} with arguments: '
            '{2}'.format(self.name, self.repository, self.body)
        )

    def do_action(self):
        """
        Snapshot indices in `index_list.indices`, with options passed.
        """
        if not self.skip_repo_fs_check:
            test_repo_fs(self.client, self.repository)
        if snapshot_running(self.client, self.repository):
            raise SnapshotInProgress('Snapshot already in progress.')
        try:
            self.loggit.info('Creating snapshot "{0}" from indices: '
                '{1}'.format(self.name, self.index_list.indices)
            )
            # Always set wait_for_completion to False. Let 'wait_for_it' do its
            # thing if wait_for_completion is set to True. Report the task_id
            # either way.
            self.client.snapshot.create(
                repository=self.repository, snapshot=self.name, body=self.body,
                wait_for_completion=False
            )
            if self.wait_for_completion:
                wait_for_it(
                    self.client, 'snapshot', snapshot=self.name, 
                    repository=self.repository, 
                    wait_interval=self.wait_interval, max_wait=self.max_wait
                )
            else:
                self.loggit.warn(
                    '"wait_for_completion" set to {0}.'
                    'Remember to check for successful completion '
                    'manually.'.format(self.wait_for_completion)
                )
        except Exception as e:
            report_failure(e)

class Restore(object):
    def __init__(self, slo, name=None, indices=None, include_aliases=False,
                ignore_unavailable=False, include_global_state=True,
                partial=False, rename_pattern=None, rename_replacement=None,
                extra_settings={}, wait_for_completion=True, wait_interval=9,
                max_wait=-1, skip_repo_fs_check=False):
        """
        :arg slo: A :class:`curator.snapshotlist.SnapshotList` object
        :arg name: Name of the snapshot to restore.  If no name is provided, it
            will restore the most recent snapshot by age.
        :type name: str
        :arg indices: A list of indices to restore.  If no indices are provided,
            it will restore all indices in the snapshot.
        :type indices: list
        :arg include_aliases: If set to `True`, restore aliases with the
            indices. (default: `False`)
        :type include_aliases: bool
        :arg ignore_unavailable: Ignore unavailable shards/indices.
            (default: `False`)
        :type ignore_unavailable: bool
        :arg include_global_state: Store cluster global state with snapshot.
            (default: `True`)
        :type include_global_state: bool
        :arg partial: Do not fail if primary shard is unavailable. (default:
            `False`)
        :type partial: bool
        :arg rename_pattern: A regular expression pattern with one or more
            captures, e.g. ``index_(.+)``
        :type rename_pattern: str
        :arg rename_replacement: A target index name pattern with `$#` numbered
            references to the captures in ``rename_pattern``, e.g.
            ``restored_index_$1``
        :type rename_replacement: str
        :arg extra_settings: Extra settings, including shard count and settings
            to omit. For more information see
            https://www.elastic.co/guide/en/elasticsearch/reference/current/modules-snapshots.html#_changing_index_settings_during_restore
        :type extra_settings: dict, representing the settings.
        :arg wait_for_completion: Wait (or not) for the operation
            to complete before returning.  (default: `True`)
        :arg wait_interval: How long in seconds to wait between checks for 
            completion.
        :arg max_wait: Maximum number of seconds to `wait_for_completion`
        :type wait_for_completion: bool

        :arg skip_repo_fs_check: Do not validate write access to repository on
            all cluster nodes before proceeding. (default: `False`).  Useful for
            shared filesystems where intermittent timeouts can affect
            validation, but won't likely affect snapshot success.
        :type skip_repo_fs_check: bool
        """
        self.loggit = logging.getLogger('curator.actions.snapshot')
        verify_snapshot_list(slo)
        # Get the most recent snapshot.
        most_recent = slo.most_recent()
        self.loggit.debug('"most_recent" snapshot: {0}'.format(most_recent))
        #: Instance variable.
        #: Will use a provided snapshot name, or the most recent snapshot in slo
        self.name = name if name else most_recent
        # Stop here now, if it's not a successful snapshot.
        if slo.snapshot_info[self.name]['state'] == 'PARTIAL' \
            and partial == True:
            self.loggit.warn(
                'Performing restore of snapshot in state PARTIAL.')
        elif slo.snapshot_info[self.name]['state'] != 'SUCCESS':
            raise CuratorException(
                'Restore operation can only be performed on snapshots with '
                'state "SUCCESS", or "PARTIAL" if partial=True.'
            )
        #: Instance variable.
        #: The Elasticsearch Client object derived from `slo`
        self.client              = slo.client
        #: Instance variable.
        #: Internal reference to `slo`
        self.snapshot_list = slo
        #: Instance variable.
        #: `repository` derived from `slo`
        self.repository          = slo.repository

        if indices:
            self.indices = ensure_list(indices)
        else:
            self.indices = slo.snapshot_info[self.name]['indices']
        self.wfc                 = wait_for_completion
        #: Instance variable
        #: How many seconds to wait between checks for completion.
        self.wait_interval = wait_interval
        #: Instance variable.
        #: How long in seconds to `wait_for_completion` before returning with an
        #: exception. A value of -1 means wait forever.
        self.max_wait   = max_wait        
        #: Instance variable version of ``rename_pattern``
        self.rename_pattern = rename_pattern if rename_replacement is not None \
            else ''
        #: Instance variable version of ``rename_replacement``
        self.rename_replacement = rename_replacement if rename_replacement \
            is not None else ''
        #: Also an instance variable version of ``rename_replacement``
        #: but with Java regex group designations of ``$#``
        #: converted to Python's ``\\#`` style.
        self.py_rename_replacement = self.rename_replacement.replace('$', '\\')
        #: Instance variable.
        #: Internally accessible copy of `skip_repo_fs_check`
        self.skip_repo_fs_check  = skip_repo_fs_check

        #: Instance variable.
        #: Populated at instance creation time from the other options
        self.body                = {
                'indices' : self.indices,
                'include_aliases' : include_aliases,
                'ignore_unavailable' : ignore_unavailable,
                'include_global_state' : include_global_state,
                'partial' : partial,
                'rename_pattern' : self.rename_pattern,
                'rename_replacement' : self.rename_replacement,
            }
        if extra_settings:
            self.loggit.debug(
                'Adding extra_settings to restore body: '
                '{0}'.format(extra_settings)
            )
            try:
                self.body.update(extra_settings)
            except:
                self.loggit.error(
                    'Unable to apply extra settings to restore body')
        self.loggit.debug('REPOSITORY: {0}'.format(self.repository))
        self.loggit.debug('WAIT_FOR_COMPLETION: {0}'.format(self.wfc))
        self.loggit.debug(
            'SKIP_REPO_FS_CHECK: {0}'.format(self.skip_repo_fs_check))
        self.loggit.debug('BODY: {0}'.format(self.body))
        # Populate the expected output index list.
        self._get_expected_output()

    def _get_expected_output(self):
        if not self.rename_pattern and not self.rename_replacement:
            self.expected_output = self.indices
            return # Don't stick around if we're not replacing anything
        self.expected_output = []
        for index in self.indices:
            self.expected_output.append(
                re.sub(
                    self.rename_pattern,
                    self.py_rename_replacement,
                    index
                )
            )
            self.loggit.debug('index: {0} replacement: '
                '{1}'.format(index, self.expected_output[-1])
            )

    def report_state(self):
        """
        Log the state of the restore
        This should only be done if ``wait_for_completion`` is `True`, and only
        after completing the restore.
        """
        all_indices = get_indices(self.client)
        found_count = 0
        missing = []
        for index in self.expected_output:
            if index in all_indices:
                found_count += 1
                self.loggit.info('Found restored index {0}'.format(index))
            else:
                missing.append(index)
        if found_count == len(self.expected_output):
            self.loggit.info('All indices appear to have been restored.')
        else:
            self.loggit.error(
                'Some of the indices do not appear to have been restored. '
                'Missing: {0}'.format(missing)
            )

    def do_dry_run(self):
        """
        Log what the output would be, but take no action.
        """
        logger.info('DRY-RUN MODE.  No changes will be made.')
        logger.info(
            'DRY-RUN: restore: Repository: {0} Snapshot name: {1} Arguments: '
            '{2}'.format(
                self.name, self.repository,
                { 'wait_for_completion' : self.wfc, 'body' : self.body }
            )
        )

        for index in self.indices:
            if self.rename_pattern and self.rename_replacement:
                replacement_msg = 'as {0}'.format(
                    re.sub(
                        self.rename_pattern,
                        self.py_rename_replacement,
                        index
                    )
                )
            else:
                replacement_msg = ''
            logger.info(
                'DRY-RUN: restore: Index {0} {1}'.format(index, replacement_msg)
            )


    def do_action(self):
        """
        Restore indices with options passed.
        """
        if not self.skip_repo_fs_check:
            test_repo_fs(self.client, self.repository)
        if snapshot_running(self.client, self.repository):
            raise SnapshotInProgress(
                'Cannot restore while a snapshot is in progress.')
        try:
            self.loggit.info('Restoring indices "{0}" from snapshot: '
                '{1}'.format(self.indices, self.name)
            )
            # Always set wait_for_completion to False. Let 'wait_for_it' do its
            # thing if wait_for_completion is set to True. Report the task_id
            # either way.
            self.client.snapshot.restore(
                repository=self.repository, snapshot=self.name, body=self.body,
                wait_for_completion=False
            )
            if self.wfc:
                wait_for_it(
                    self.client, 'restore', index_list=self.expected_output,
                    wait_interval=self.wait_interval, max_wait=self.max_wait
                )
            else:
                self.loggit.warn(
                    '"wait_for_completion" set to {0}. '
                    'Remember to check for successful completion '
                    'manually.'.format(self.wfc)
                )
        except Exception as e:
            report_failure(e)
