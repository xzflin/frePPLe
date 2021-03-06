#
# Copyright (C) 2007-2013 by frePPLe bvba
#
# This library is free software; you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero
# General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import Permission
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS

from freppledb.common.models import User, Scenario


class MultiDBBackend(ModelBackend):
  '''
  This customized authentication is based on the Django ModelBackend
  with the following extensions:
    - We allow a user to log in using either their user name or
      their email address.
    - Permissions are scenario-specific.

  This authentication backend relies on the MultiDBMiddleware class
  to assure that the user object refers to the correct database.
  '''
  def authenticate(self, username=None, password=None):
    try:
      validate_email(username)
      # The user name looks like an email address
      user = User.objects.get(email=username)
      if user.check_password(password):
        return user
    except User.DoesNotExist:
      # Run the default password hasher once to reduce the timing
      # difference between an existing and a non-existing user.
      # See django ticket #20760
      User().set_password(password)
    except ValidationError:
      # The user name isn't an email address
      try:
        user = User.objects.get(username=username)
        if user.check_password(password):
          return user
      except User.DoesNotExist:
        # Run the default password hasher once to reduce the timing
        # difference between an existing and a non-existing user.
        # See django ticket #20760
        User().set_password(password)


  def _get_user_permissions(self, user_obj):
    return user_obj.user_permissions.all()


  def _get_group_permissions(self, user_obj):
    user_groups_field = User._meta.get_field('groups')
    user_groups_query = 'group__%s' % user_groups_field.related_query_name()
    return Permission.objects.using(user_obj._state.db).filter(**{user_groups_query: user_obj})


  def _get_permissions(self, user_obj, obj, from_name):
    """
    Returns the permissions of `user_obj` from `from_name`. `from_name` can
    be either "group" or "user" to return permissions from
    `_get_group_permissions` or `_get_user_permissions` respectively.
    """
    if not user_obj.is_active or user_obj.is_anonymous() or obj is not None:
      return set()

    perm_cache_name = '_%s_perm_cache_%s' % (from_name, user_obj._state.db)
    if not hasattr(user_obj, perm_cache_name):
      if user_obj.is_superuser:
        perms = Permission.objects.using(user_obj._state.db).all()
      else:
        perms = getattr(self, '_get_%s_permissions' % from_name)(user_obj)
      perms = perms.values_list('content_type__app_label', 'codename').order_by()
      setattr(user_obj, perm_cache_name, set("%s.%s" % (ct, name) for ct, name in perms))
    return getattr(user_obj, perm_cache_name)


  def get_all_permissions(self, user_obj, obj=None):
    if not user_obj.is_active or user_obj.is_anonymous() or obj is not None:
      return set()
    if not hasattr(user_obj, '_perm_cache_%s' % user_obj._state.db):
      user_obj._perm_cache = self.get_user_permissions(user_obj)
      user_obj._perm_cache.update(self.get_group_permissions(user_obj))
    return user_obj._perm_cache


  def get_user(self, user_id):
    try:
      user = User.objects.get(pk=user_id)

      # Now populate a dictionary with scenarios in which the user is active, and
      # whether he's a superuser in them.
      user.scenarios = {}
      if user.is_active:
        user.scenarios[DEFAULT_DB_ALIAS] = user.is_superuser
      for db in Scenario.objects.filter(status='In use').values('name'):
        if db['name'] == DEFAULT_DB_ALIAS:
          # Already populated above
          continue
        try:
          user2 = User.objects.using(db['name']).get(username=user.username)
          if user2.is_active:
            user.scenarios[db['name']] = user2.is_superuser
        except:
          # Silently ignore errors. Eg user doesn't exist in scenario
          pass
      return user
    except User.DoesNotExist:
      return None
